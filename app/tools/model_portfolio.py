"""CLI der Portfoliokonstruktion (Spec 12.2/12.3).

Usage:
    python -m app.tools.model_portfolio build [--snapshot YYYY-MM-DD]
                                              [--mode full|interim|monitor]
                                              [--dry-run] [--out DIR]
    python -m app.tools.model_portfolio compare [--v1] [--v2]
                                                [--snapshot YYYY-MM-DD]

``build`` erzeugt das Zielportfolio ohne UI, schreibt (außer bei
``--dry-run``) in ``model_portfolio`` und gibt einen Markdown-Report
``reports/modellportfolio_YYYY-MM-DD.md`` aus (Kopfdaten, Diagnosen,
Trade-Liste, Exposures). ``compare`` vergleicht die Rangfolgen v1
(``total_score``) und v2 (``composite_z``). Exit-Codes: 0 ohne
Diagnose-Fehler, 1 bei Warnungen, 2 bei Fehlern.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from app.core import persistence
from app.core.diagnostics import (
    SEV_ERROR,
    SEV_WARNING,
    Diagnostic,
    count_by_severity,
    sort_diagnostics,
)
from app.core.portfolio_construction import (
    ACTION_HOLD,
    build_model_portfolio,
    load_benchmark_weights,
    load_risk_cache,
)
from app.core.scoring_v2 import V2_FACTOR_NAMES, compute_scores_v2
from app.core.state import STATE


def _fmt(value: float | None, digits: int = 1, percent: bool = False) -> str:
    """Deutsche Zahlformatierung (Dezimalkomma)."""
    if value is None or pd.isna(value):
        return "–"
    scaled = value * 100 if percent else value
    return f"{scaled:.{digits}f}".replace(".", ",") + (" %" if percent else "")


def _load_universe(snapshot: str | None) -> tuple[pd.DataFrame | None, date]:
    STATE.load_from_db()
    if snapshot:
        snap = date.fromisoformat(snapshot)
        frame = persistence.load_universe_snapshot(snap)
        if frame is not None and "composite_z" not in frame.columns:
            frame, _ = compute_scores_v2(
                frame, STATE.settings, overrides=persistence.load_overrides(),
                snapshot_date=snap,
            )
        return frame, snap
    frame = STATE.scored if STATE.scored is not None else pd.DataFrame()
    if frame.empty:
        return None, date.today()
    from app.core.signal_events import snapshot_date_from_universe

    return frame, snapshot_date_from_universe(STATE.raw, None)


def _exit_code(diags: list[Diagnostic]) -> int:
    counts = count_by_severity(diags)
    if counts.get(SEV_ERROR):
        return 2
    if counts.get(SEV_WARNING):
        return 1
    return 0


def _exposures_lines(
    portfolio: pd.DataFrame,
    universe: pd.DataFrame,
    settings,
    snap: date,
) -> list[str]:
    lines: list[str] = ["## Exposures", ""]
    uni = universe.copy()
    if "uid" in uni.columns:
        uni.index = pd.Index(uni["uid"].astype(str), name="_uid")
    pf = portfolio.set_index("uid")
    joined = pf.join(uni[[c for c in ("sector", "region") if c in uni.columns]])
    benchmark = load_benchmark_weights(
        settings,
        universe_regions=sorted(
            uni.get("region", pd.Series(dtype=str)).dropna().unique()
        ),
        asof=snap,
        universe=uni,
    )
    for dim, bm in (("sector", benchmark.sector), ("region", benchmark.region)):
        if dim not in joined.columns:
            continue
        title = "Sektoren" if dim == "sector" else "Regionen"
        band = settings.pc_sector_band if dim == "sector" else settings.pc_region_band
        lines.append(
            f"### {title} (Band ± {_fmt(band, 0, percent=True)}"
            + (", Benchmark-Restriktion ausgesetzt" if bm is None else "")
            + ")"
        )
        lines.append("")
        lines.append(f"| {title[:-2]} | Portfolio | Benchmark | aktiv |")
        lines.append("|---|---|---|---|")
        agg = joined.groupby(joined[dim].fillna("Unbekannt"))[
            "weight_effective"
        ].sum()
        names = sorted(set(agg.index) | set((bm or {}).keys()))
        for name in names:
            w = float(agg.get(name, 0.0))
            b = float((bm or {}).get(name, 0.0))
            lines.append(
                f"| {name} | {_fmt(w, 1, True)} | {_fmt(b, 1, True)} | "
                f"{_fmt(w - b, 1, True)} |"
            )
        lines.append("")

    # Faktor-Exposure-Plausibilisierung: Ø z_* Portfolio vs. Universum.
    z_cols = [f"z_{f}" for f in V2_FACTOR_NAMES if f"z_{f}" in uni.columns]
    if z_cols:
        lines.append("### Faktor-Exposure (Ø Z-Score)")
        lines.append("")
        lines.append("| Faktor | Portfolio | Universum |")
        lines.append("|---|---|---|")
        pf_rows = uni.loc[[u for u in pf.index if u in uni.index]]
        for col in z_cols:
            lines.append(
                f"| {col[2:]} | {_fmt(pf_rows[col].mean(), 2)} | "
                f"{_fmt(uni[col].mean(), 2)} |"
            )
        lines.append("")
    return lines


def _write_build_report(
    result: dict, snap: date, out_dir: Path, universe: pd.DataFrame, settings
) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"modellportfolio_{snap.isoformat()}.md"
    meta = result["meta"]
    diags = sort_diagnostics(result["diagnostics"])
    counts = count_by_severity(diags)
    lines = [
        f"# Modellportfolio — Snapshot {snap.isoformat()}",
        "",
        f"- Rebalance-Modus: **{meta['rebalance_mode']}**",
        f"- Titel: {meta['n_titles']}",
        f"- Ex-ante-TE: {_fmt(meta['te_ex_ante'], 2, percent=True)}"
        + (
            f" (Kursabdeckung {_fmt(meta['te_coverage'], 0, percent=True)})"
            if meta.get("te_coverage") is not None
            else ""
        ),
        f"- Turnover (einseitig): {_fmt(meta['turnover_oneway'], 1, percent=True)}",
        f"- Trades: {meta['n_trades']} (davon verschoben: {meta['n_deferred']})",
        f"- Diagnosen: {counts[SEV_ERROR]} Fehler / {counts[SEV_WARNING]} "
        f"Warnungen / {counts['Info']} Infos",
        f"- Settings-Hash: `{meta['settings_hash'][:16]}…`",
        "",
        "## Diagnosen",
        "",
    ]
    if diags:
        lines.append("| Schweregrad | Code | Titel | Meldung |")
        lines.append("|---|---|---|---|")
        for d in diags:
            lines.append(
                f"| {d.severity} | {d.code} | {d.uid or '–'} | {d.message} |"
            )
    else:
        lines.append("Keine Diagnosen.")
    lines.append("")

    trades = result["trades"].trades
    lines.append("## Trade-Liste")
    lines.append("")
    if trades.empty:
        lines.append("Keine Trades.")
    else:
        active = trades[trades["action"] != ACTION_HOLD]
        if active.empty:
            lines.append("Keine Trades (alle Positionen HALTEN).")
        else:
            lines.append(
                "| Titel | Aktion | aktuell | Ziel | Δw | Grund | Death Cross |"
            )
            lines.append("|---|---|---|---|---|---|---|")
            for _, r in active.iterrows():
                lines.append(
                    f"| {r['uid']} | {r['action']} | "
                    f"{_fmt(r['weight_current'], 1, True)} | "
                    f"{_fmt(r['weight_target'], 1, True)} | "
                    f"{_fmt(r['delta_w'], 1, True)} | {r['reason']} | "
                    f"{'⚠' if r.get('trend_warning') else '–'} |"
                )
    lines.append("")

    portfolio = result["portfolio"]
    if not portfolio.empty:
        lines.append("## Zielportfolio")
        lines.append("")
        lines.append(
            "| Titel | composite_z | Perzentil | Zone | w_model | w_effective |"
        )
        lines.append("|---|---|---|---|---|---|")
        ordered = portfolio.sort_values("weight_effective", ascending=False)
        for _, r in ordered.iterrows():
            lines.append(
                f"| {r['uid']} | {_fmt(r['composite_z'], 2)} | "
                f"{_fmt(r['composite_pct'], 0, True)} | {r['zone_v2']} | "
                f"{_fmt(r['weight_model'], 1, True)} | "
                f"{_fmt(r['weight_effective'], 1, True)} |"
            )
        lines.append("")
        lines.extend(_exposures_lines(portfolio, universe, settings, snap))

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _cmd_build(args: argparse.Namespace) -> int:
    universe, snap = _load_universe(args.snapshot)
    if universe is None or universe.empty:
        print(
            "Kein Universum verfügbar — Import durchführen oder --snapshot "
            "angeben.",
            file=sys.stderr,
        )
        return 2
    settings = STATE.settings
    current = STATE.portfolio_weights()
    overrides = persistence.load_overrides()
    last_meta = persistence.load_model_portfolio_meta()
    uids = sorted(set(universe.get("uid", pd.Series(dtype=str)).astype(str)))
    risk_cache = load_risk_cache(uids, settings, asof=snap)

    result = build_model_portfolio(
        universe,
        settings,
        current,
        mode=args.mode,
        snapshot_date=snap,
        overrides=overrides,
        risk_cache=risk_cache,
        last_meta=last_meta,
    )

    if not args.dry_run and result["mode"] != "monitor":
        persistence.save_model_portfolio(result["portfolio"], result["meta"], snap)
        print(f"Zielportfolio gespeichert (model_portfolio, {snap.isoformat()}).")
    elif args.dry_run:
        print("Dry-Run — nichts gespeichert.")

    out_dir = Path(args.out or settings.risk_report_dir)
    path = _write_build_report(result, snap, out_dir, universe, settings)
    print(f"Report geschrieben: {path}")
    meta = result["meta"]
    print(
        f"Modus: {result['mode']} · Titel: {meta['n_titles']} · "
        f"TE: {_fmt(meta['te_ex_ante'], 2, percent=True)} · "
        f"Turnover: {_fmt(meta['turnover_oneway'], 1, percent=True)}"
    )
    return _exit_code(result["diagnostics"])


def _cmd_compare(args: argparse.Namespace) -> int:
    universe, snap = _load_universe(args.snapshot)
    if universe is None or universe.empty:
        print("Kein Universum verfügbar.", file=sys.stderr)
        return 2
    df = universe
    if "total_score" not in df.columns or "composite_z" not in df.columns:
        print(
            "Universum enthält nicht beide Score-Versionen (total_score, "
            "composite_z).",
            file=sys.stderr,
        )
        return 2
    both = df[["uid", "total_score", "composite_z", "sector"]].dropna(
        subset=["total_score", "composite_z"]
    )
    if len(both) < 5:
        print("Zu wenige Titel mit beiden Scores.", file=sys.stderr)
        return 2
    rho = both["total_score"].corr(both["composite_z"], method="spearman")
    pct_v1 = both["total_score"].rank(pct=True)
    pct_v2 = both["composite_z"].rank(pct=True)
    shift = (pct_v2 - pct_v1) * 100
    movers = both.assign(rangaenderung=shift)
    movers = movers[movers["rangaenderung"].abs() > 30].sort_values(
        "rangaenderung", key=lambda s: s.abs(), ascending=False
    )

    print(f"# Vergleich v1/v2 — Snapshot {snap.isoformat()}")
    print(f"Spearman-Korrelation (total_score vs. composite_z): "
          f"{_fmt(rho, 3)}")
    print(f"Titel mit Rangänderung > 30 Perzentilpunkte: {len(movers)}")
    for _, r in movers.iterrows():
        print(
            f"  {r['uid']}: {_fmt(r['rangaenderung'], 0)} Punkte "
            f"({r['sector']})"
        )
    for label, column, ascending in (
        ("v1 (total_score)", "total_score", False),
        ("v2 (composite_z)", "composite_z", False),
    ):
        top = both.sort_values(column, ascending=ascending).head(35)
        counts = top["sector"].value_counts()
        print(f"Sektorverteilung Top-35 {label}:")
        for sector, n in counts.items():
            print(f"  {sector}: {n}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING, format="[warn] %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_build = sub.add_parser("build", help="Zielportfolio erzeugen")
    p_build.add_argument("--snapshot", help="Archivierter Snapshot (YYYY-MM-DD)")
    p_build.add_argument("--mode", choices=["full", "interim", "monitor"])
    p_build.add_argument("--dry-run", action="store_true")
    p_build.add_argument("--out", help="Report-Verzeichnis (Default: reports/)")
    p_build.set_defaults(func=_cmd_build)

    p_cmp = sub.add_parser("compare", help="Rangfolgen v1 und v2 vergleichen")
    p_cmp.add_argument("--v1", action="store_true", help="(Kompatibilität)")
    p_cmp.add_argument("--v2", action="store_true", help="(Kompatibilität)")
    p_cmp.add_argument("--snapshot", help="Archivierter Snapshot (YYYY-MM-DD)")
    p_cmp.set_defaults(func=_cmd_compare)

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 2
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
