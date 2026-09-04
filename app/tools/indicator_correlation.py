"""Indikator-Korrelationsanalyse für den v1→v2-Übergang (Spec 12.1).

Usage:
    python -m app.tools.indicator_correlation [--snapshot YYYY-MM-DD] [--out DIR]

Berechnet auf dem aktuellen oder einem archivierten Snapshot die
Spearman-Rangkorrelationsmatrix aller ``z_*``-Indikatoren (Composite v2)
und aller v1-Indikator-Perzentile, getrennt für Nicht-Financials und
Financials, dazu ein hierarchisches Clustering (average linkage, Distanz
1 − |ρ|, Schwelle |ρ| ≥ 0,8) und die Datenabdeckung je Indikator.
Ausgabe: ``reports/indikator_korrelation_YYYY-MM-DD.md`` plus CSVs.
Exit-Codes: 0 Erfolg, 1 fehlende Daten, 2 Argument-/Konfigurationsfehler.

Dieses Werkzeug ist vor der Produktivsetzung von v2 einmal auszuführen;
das Ergebnis wird im Projekt abgelegt.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import persistence
from app.core.piotroski import is_financial_sector
from app.core.scoring import INDICATOR_TO_COLUMN, _indicator_percentile
from app.core.scoring_v2 import compute_scores_v2
from app.core.state import STATE

# |ρ| ≥ 0,8 gilt als redundant → Distanzschwelle 1 − 0,8 = 0,2.
CLUSTER_RHO_THRESHOLD = 0.8


def _load_frame(snapshot: str | None) -> tuple[pd.DataFrame | None, date]:
    STATE.load_from_db()
    if snapshot:
        snap_date = date.fromisoformat(snapshot)
        frame = persistence.load_universe_snapshot(snap_date)
        return frame, snap_date
    frame = STATE.scored if STATE.scored is not None else pd.DataFrame()
    return (None if frame.empty else frame), date.today()


def _ensure_v2(frame: pd.DataFrame) -> pd.DataFrame:
    if "composite_z" in frame.columns:
        return frame
    out, _ = compute_scores_v2(frame, STATE.settings)
    return out


def _v1_percentiles(frame: pd.DataFrame) -> pd.DataFrame:
    """Perzentile je v1-Indikator (identische Logik wie im v1-Scoring)."""
    out = pd.DataFrame(index=frame.index)
    for indicator, column in INDICATOR_TO_COLUMN.items():
        if column in frame.columns:
            out[f"v1_pct_{indicator}"] = _indicator_percentile(
                frame, column, STATE.settings
            )
    return out


def average_linkage_clusters(
    corr: pd.DataFrame, rho_threshold: float = CLUSTER_RHO_THRESHOLD
) -> list[list[str]]:
    """Hierarchisches Clustering ohne scipy: average linkage auf D = 1 − |ρ|.

    Merged wiederholt das Cluster-Paar mit der kleinsten mittleren
    Inter-Cluster-Distanz, solange diese ≤ 1 − ``rho_threshold`` ist.
    """
    cols = list(corr.columns)
    dist = 1.0 - corr.abs().to_numpy()
    np.fill_diagonal(dist, 0.0)
    clusters: list[list[int]] = [[i] for i in range(len(cols))]
    max_dist = 1.0 - rho_threshold

    def avg_dist(a: list[int], b: list[int]) -> float:
        vals = [dist[i, j] for i in a for j in b]
        finite = [v for v in vals if np.isfinite(v)]
        return float(np.mean(finite)) if finite else np.inf

    while len(clusters) > 1:
        best = (np.inf, -1, -1)
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                d = avg_dist(clusters[i], clusters[j])
                if d < best[0]:
                    best = (d, i, j)
        d, i, j = best
        if d > max_dist:
            break
        clusters[i] = clusters[i] + clusters[j]
        del clusters[j]
    return [sorted(cols[i] for i in cluster) for cluster in clusters]


def _analyse_segment(frame: pd.DataFrame, columns: list[str]) -> dict:
    block = frame[columns]
    coverage = block.notna().mean().sort_values(ascending=False)
    # Nur Spalten mit Streuung und Mindestbesetzung korrelieren.
    usable = [
        c
        for c in columns
        if block[c].notna().sum() >= 5 and block[c].nunique(dropna=True) > 1
    ]
    corr = block[usable].corr(method="spearman", min_periods=5)
    clusters = average_linkage_clusters(corr) if usable else []
    return {"coverage": coverage, "corr": corr, "clusters": clusters}


def _write_outputs(
    results: dict[str, dict], snap_date: date, out_dir: Path
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"indikator_korrelation_{snap_date.isoformat()}.md"
    lines = [
        f"# Indikator-Korrelation — Snapshot {snap_date.isoformat()}",
        "",
        "Spearman-Rangkorrelation aller z_*-Indikatoren (Composite v2) und "
        "aller v1-Indikator-Perzentile. Clustering: average linkage auf "
        "Distanz 1 − |ρ|, Schwelle |ρ| ≥ "
        f"{CLUSTER_RHO_THRESHOLD:.1f}".replace(".", ",")
        + " (Cluster = redundante Indikatorgruppen).",
        "",
    ]
    for segment, res in results.items():
        lines.append(f"## {segment}")
        lines.append("")
        lines.append("### Cluster (|ρ| ≥ 0,8)")
        multi = [c for c in res["clusters"] if len(c) > 1]
        if multi:
            for cluster in multi:
                lines.append(f"- {', '.join(cluster)}")
        else:
            lines.append("- keine redundanten Cluster")
        lines.append("")
        lines.append("### Datenabdeckung je Indikator")
        lines.append("")
        lines.append("| Indikator | Abdeckung |")
        lines.append("|---|---|")
        for name, share in res["coverage"].items():
            lines.append(f"| {name} | {share * 100:.1f} % |".replace(".", ","))
        lines.append("")
        csv_name = (
            f"indikator_korrelation_{snap_date.isoformat()}_"
            f"{segment.lower().replace('-', '').replace(' ', '_')}.csv"
        )
        res["corr"].to_csv(out_dir / csv_name)
        lines.append(f"Korrelationsmatrix: `{csv_name}`")
        lines.append("")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", help="Archivierter Snapshot (YYYY-MM-DD)")
    parser.add_argument("--out", help="Zielverzeichnis (Default: reports/)")
    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2

    try:
        frame, snap_date = _load_frame(args.snapshot)
    except ValueError as exc:
        print(f"Ungültiges Snapshot-Datum: {exc}", file=sys.stderr)
        return 2
    if frame is None or frame.empty:
        print(
            "Kein Universum verfügbar — Import durchführen oder --snapshot "
            "auf einen archivierten Stand setzen.",
            file=sys.stderr,
        )
        return 1

    frame = _ensure_v2(frame)
    v1_pct = _v1_percentiles(frame)
    frame = pd.concat([frame, v1_pct], axis=1)

    z_cols = sorted(
        c
        for c in frame.columns
        if c.startswith("z_")
        and c not in {"z_value", "z_quality", "z_momentum", "z_investment"}
    )
    v1_cols = sorted(v1_pct.columns)
    all_cols = z_cols + v1_cols

    is_fin = is_financial_sector(frame)
    results = {
        "Nicht-Financials": _analyse_segment(frame[~is_fin], all_cols),
        "Financials": _analyse_segment(frame[is_fin], all_cols),
    }
    out_dir = Path(args.out or STATE.settings.risk_report_dir)
    md_path = _write_outputs(results, snap_date, out_dir)
    print(f"Report geschrieben: {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
