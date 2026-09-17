"""CLI der Portfoliokonstruktion (Spec 12.2/12.3).

Usage:
    python -m app.tools.model_portfolio build [--snapshot YYYY-MM-DD]
                                              [--mode full|interim|monitor]
                                              [--portfolio ID|NAME]
                                              [--dry-run] [--out DIR]
    python -m app.tools.model_portfolio compare [--v1] [--v2]
                                                [--snapshot YYYY-MM-DD]
    python -m app.tools.model_portfolio compare --scheme-a region_sector
                                                --scheme-b industry_group_then_region
                                                [--snapshot YYYY-MM-DD] [--csv DATEI]
                                                [--out DIR]

``build`` erzeugt das Zielportfolio ohne UI, schreibt (außer bei
``--dry-run``) in ``model_portfolio`` und gibt einen Markdown-Report
``reports/modellportfolio_YYYY-MM-DD.md`` aus (Kopfdaten, Diagnosen,
Trade-Liste, Exposures). Als Bestand dient das mit ``--portfolio``
gewählte hochgeladene Portfolio, sonst die auf der Modellportfolio-Seite
gespeicherte Auswahl bzw. das aktive Portfolio. ``compare`` vergleicht die
Rangfolgen v1 (``total_score``) und v2 (``composite_z``); mit
``--scheme-a/--scheme-b`` werden stattdessen zwei Neutralisierungsschemata
auf demselben Snapshot gerechnet und als Markdown-Report
``reports/neutralisierung_vergleich_<datum>.md`` verglichen (Spec
Sequenzielle Neutralisierung 8). Ein Schema ist eine Schema-ID
(``region_sector``, ``industry_group_then_region``, ``sector_then_region``,
``region_only``, ``global`` — für alle Indikatoren) oder ``settings``
(Zuordnung je Indikator aus den Einstellungen). ``--csv`` rechnet auf
einem Koyfin-Export statt auf DB/Archiv. Exit-Codes: 0 ohne
Diagnose-Fehler, 1 bei Warnungen, 2 bei Fehlern.
"""

from __future__ import annotations

import argparse
import copy
import logging
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from app.core import persistence
from app.core.config import NEUT_SCHEMES, Settings
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
from app.core.gics import industry_group_size_stats
from app.core.scoring_v2 import V2_FACTOR_NAMES, compute_scores_v2
from app.core.state import STATE

# Schema-Argument der Compare-CLI: Schema-ID für alle Indikatoren oder
# ``settings`` = Zuordnung je Indikator aus den Einstellungen.
SCHEME_FROM_SETTINGS = "settings"
COMPARE_SCHEME_CHOICES: tuple[str, ...] = (*NEUT_SCHEMES, SCHEME_FROM_SETTINGS)
# Rangänderung (Perzentilpunkte), ab der ein Titel im Report gelistet wird.
RANK_SHIFT_THRESHOLD = 20.0
TOP_N = 35


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
        "- Bestandsportfolio: " + str(meta.get("source_portfolio_name") or "–"),
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
    # Bestandsportfolio: --portfolio (ID oder Name), sonst die auf der
    # Modellportfolio-Seite gespeicherte Auswahl bzw. das aktive Portfolio.
    if getattr(args, "portfolio", None):
        found = persistence.find_ms_portfolio(args.portfolio)
        if found is None:
            print(
                f"Portfolio '{args.portfolio}' nicht gefunden — verfügbar: "
                + (
                    ", ".join(
                        f"{p['id']} ({p['name']})" for p in STATE.ms_portfolios
                    )
                    or "keine"
                ),
                file=sys.stderr,
            )
            return 2
        source_id = int(found["id"])
    else:
        source_id = STATE.model_source_portfolio_id()
    current = STATE.portfolio_weights(portfolio_id=source_id)
    source_name = STATE.portfolio_name(source_id) or (
        "Standard-Portfolio (nicht gespeichert)" if source_id is None else f"#{source_id}"
    )
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
    result["meta"]["source_portfolio_id"] = source_id
    result["meta"]["source_portfolio_name"] = source_name

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


def _scheme_settings(settings: Settings, scheme: str) -> Settings:
    """Settings-Kopie, in der alle Indikatoren ``scheme`` nutzen
    (``settings`` = unveränderte Zuordnung je Indikator)."""
    out = copy.deepcopy(settings)
    if scheme != SCHEME_FROM_SETTINGS:
        out.v2_neut_scheme_default = scheme
        out.v2_neut_scheme_by_indicator = {}
    out.validate_neut_schemes()
    return out


def _spearman(a: pd.Series, b: pd.Series, min_n: int = 5) -> float | None:
    both = pd.concat([a, b], axis=1).dropna()
    if len(both) < min_n:
        return None
    return float(both.iloc[:, 0].corr(both.iloc[:, 1], method="spearman"))


def _level_summary(frame: pd.DataFrame, uid) -> str:
    parts = []
    for col in sorted(c for c in frame.columns if c.startswith("neut_level_")):
        value = frame.at[uid, col]
        if isinstance(value, str) and value:
            parts.append(f"{col[len('neut_level_'):]}={value}")
    return "; ".join(parts) or "–"


def _fallback_table(frame: pd.DataFrame, settings: Settings) -> list[dict]:
    """Fallback-Statistik je Indikator (Report-Abschnitt 5)."""
    from app.core.scoring_v2 import _INTENDED_LEVEL1

    rows: list[dict] = []
    for col in sorted(c for c in frame.columns if c.startswith("neut_level_")):
        name = col[len("neut_level_"):]
        used = frame[col].dropna().astype(str)
        if used.empty:
            continue
        scheme = settings.neut_scheme_for(name)
        intended = _INTENDED_LEVEL1.get(scheme, "")
        level1 = used.str.split(">").str[0]
        has_l2 = scheme in ("industry_group_then_region", "sector_then_region")
        rows.append(
            {
                "indikator": name,
                "schema": scheme,
                "n": int(len(used)),
                "ziel": int((level1 == intended).sum()),
                "sektor": int((level1 == "sector").sum()) if intended != "sector" else 0,
                "global": int((level1 == "global").sum()) if intended != "global" else 0,
                "l2_entfallen": int((~used.str.contains(">region", regex=False)).sum()) if has_l2 else None,
            }
        )
    return rows


def compare_schemes(
    universe: pd.DataFrame,
    settings: Settings,
    scheme_a: str,
    scheme_b: str,
    snapshot_date: date | None = None,
    overrides: pd.DataFrame | None = None,
) -> dict:
    """Rechnet beide Composites auf demselben Snapshot und liefert die
    Vergleichsgrößen des Reports (Spec Sequenzielle Neutralisierung 8)."""
    settings_a = _scheme_settings(settings, scheme_a)
    settings_b = _scheme_settings(settings, scheme_b)
    out_a, diags_a = compute_scores_v2(
        universe, settings_a, overrides=overrides, snapshot_date=snapshot_date
    )
    out_b, diags_b = compute_scores_v2(
        universe, settings_b, overrides=overrides, snapshot_date=snapshot_date
    )
    for frame in (out_a, out_b):
        frame.index = pd.Index(frame["uid"].astype(str), name="_uid")

    meta_cols = [c for c in ("name", "sector", "industry_group", "region") if c in out_b.columns]
    joined = pd.DataFrame(
        {
            "z_a": out_a["composite_z"],
            "z_b": out_b["composite_z"].reindex(out_a.index),
        }
    )
    for col in meta_cols:
        joined[col] = out_b[col].reindex(out_a.index)
    both = joined.dropna(subset=["z_a", "z_b"]).copy()
    both["pct_a"] = both["z_a"].rank(pct=True, method="average") * 100
    both["pct_b"] = both["z_b"].rank(pct=True, method="average") * 100
    both["shift"] = both["pct_b"] - both["pct_a"]

    rho_total = _spearman(both["z_a"], both["z_b"])
    rho_by: dict[str, dict[str, tuple[float | None, int]]] = {}
    for dim in ("sector", "region"):
        if dim not in both.columns:
            continue
        rho_by[dim] = {}
        for key, grp in both.groupby(both[dim].fillna("Unbekannt"), sort=True):
            rho_by[dim][str(key)] = (_spearman(grp["z_a"], grp["z_b"]), int(len(grp)))

    movers = both[both["shift"].abs() > RANK_SHIFT_THRESHOLD].sort_values(
        "shift", key=lambda s: s.abs(), ascending=False
    )
    movers = movers.assign(
        levels_a=[_level_summary(out_a, uid) for uid in movers.index],
        levels_b=[_level_summary(out_b, uid) for uid in movers.index],
    )

    top_a = both.sort_values(["z_a"], ascending=False).head(TOP_N)
    top_b = both.sort_values(["z_b"], ascending=False).head(TOP_N)
    top_a_ids = sorted(top_a.index)
    top_b_ids = sorted(top_b.index)
    composition: dict[str, dict[str, dict[str, int]]] = {}
    for dim in ("sector", "industry_group", "region"):
        if dim not in both.columns:
            continue
        composition[dim] = {
            "a": top_a[dim].fillna("Unbekannt").value_counts().to_dict(),
            "b": top_b[dim].fillna("Unbekannt").value_counts().to_dict(),
        }

    return {
        "scheme_a": scheme_a,
        "scheme_b": scheme_b,
        "snapshot_date": snapshot_date,
        "n_titles": int(len(both)),
        "rho_total": rho_total,
        "rho_by": rho_by,
        "movers": movers,
        "top_a": top_a_ids,
        "top_b": top_b_ids,
        "top_intersection": sorted(set(top_a_ids) & set(top_b_ids)),
        "composition": composition,
        "group_sizes_b": industry_group_size_stats(out_b, int(settings_b.v2_min_group_size_l1)),
        "fallback_b": _fallback_table(out_b, settings_b),
        "diagnostics_a": diags_a,
        "diagnostics_b": diags_b,
        "hash_a": settings_a.neut_scheme_hash(),
        "hash_b": settings_b.neut_scheme_hash(),
        "frame_a": out_a,
        "frame_b": out_b,
    }


def _rho_hint(rho: float | None) -> str:
    if rho is None:
        return "–"
    if rho > 0.90:
        return "> 0,90 → Umstellung ändert wenig"
    if rho >= 0.75:
        return "0,75–0,90 → spürbar, Dokumentation der Entscheidung erforderlich"
    return "< 0,75 → vor Umstellung Gruppengrößen und Mapping prüfen"


def build_neutralization_report(result: dict, source_note: str = "") -> str:
    """Markdown-Report des Schema-Vergleichs (fünf Pflicht-Abschnitte)."""
    snap = result.get("snapshot_date")
    snap_text = snap.isoformat() if isinstance(snap, date) else "–"
    a, b = result["scheme_a"], result["scheme_b"]
    lines: list[str] = [
        f"# Neutralisierung — Vergleich {a} vs. {b} (Snapshot {snap_text})",
        "",
        f"- Schema A: `{a}` (Hash `{result['hash_a'][:16]}…`)",
        f"- Schema B: `{b}` (Hash `{result['hash_b'][:16]}…`)",
        f"- Titel mit Composite in beiden Schemata: {result['n_titles']}",
    ]
    if source_note:
        lines.append(f"- Datenquelle: {source_note}")
    lines += [
        "",
        "**Interpretationshinweis:** Rangkorrelation > 0,90 → Umstellung ändert "
        "wenig; 0,75–0,90 → spürbar, Dokumentation der Entscheidung "
        "erforderlich; < 0,75 → vor Umstellung Gruppengrößen und Mapping prüfen.",
        "",
        "## 1. Spearman-Rangkorrelation der composite_z",
        "",
        f"- Gesamt: **{_fmt(result['rho_total'], 3)}** ({_rho_hint(result['rho_total'])})",
        "",
    ]
    for dim, title in (("sector", "Je Sektor"), ("region", "Je Region")):
        table = result["rho_by"].get(dim)
        if not table:
            continue
        lines += [f"### {title}", "", f"| {title[3:]} | n | ρ |", "|---|---|---|"]
        for key, (rho, n) in table.items():
            lines.append(f"| {key} | {n} | {_fmt(rho, 3)} |")
        lines.append("")

    movers = result["movers"]
    lines += [
        f"## 2. Titel mit Rangänderung > {_fmt(RANK_SHIFT_THRESHOLD, 0)} Perzentilpunkte",
        "",
        f"Anzahl: **{len(movers)}** von {result['n_titles']}.",
        "",
    ]
    if not movers.empty:
        lines += [
            "| Titel | Name | Sektor | Industriegruppe | Region | Pct A | Pct B | Δ | z A | z B | Ebenen A | Ebenen B |",
            "|---|---|---|---|---|---|---|---|---|---|---|---|",
        ]
        for uid, r in movers.iterrows():
            lines.append(
                f"| {uid} | {r.get('name', '')} | {r.get('sector', '')} | "
                f"{r.get('industry_group', '')} | {r.get('region', '')} | "
                f"{_fmt(r['pct_a'], 0)} | {_fmt(r['pct_b'], 0)} | {_fmt(r['shift'], 0)} | "
                f"{_fmt(r['z_a'], 2)} | {_fmt(r['z_b'], 2)} | {r['levels_a']} | {r['levels_b']} |"
            )
        lines.append("")

    lines += [f"## 3. Zusammensetzung der Top-{TOP_N}", ""]
    for dim, title in (
        ("sector", "Sektor"),
        ("industry_group", "Industriegruppe"),
        ("region", "Region"),
    ):
        comp = result["composition"].get(dim)
        if not comp:
            continue
        keys = sorted(set(comp["a"]) | set(comp["b"]))
        lines += [f"### {title}", "", f"| {title} | Schema A | Schema B |", "|---|---|---|"]
        for key in keys:
            lines.append(f"| {key} | {comp['a'].get(key, 0)} | {comp['b'].get(key, 0)} |")
        lines.append("")
    inter = result["top_intersection"]
    lines += [
        f"Schnittmenge der Top-{TOP_N}: **{len(inter)}** Titel"
        + (f" ({', '.join(inter)})" if inter else ""),
        "",
        "Nur in A: " + (", ".join(sorted(set(result["top_a"]) - set(inter))) or "–"),
        "",
        "Nur in B: " + (", ".join(sorted(set(result["top_b"]) - set(inter))) or "–"),
        "",
        "## 4. Gruppengrößenstatistik (Schema B)",
        "",
    ]
    stats = result.get("group_sizes_b")
    if stats:
        lines += [
            f"- Industriegruppen: {stats['n_groups']}",
            f"- Größe: min {stats['min']} / Median {_fmt(stats['median'], 0)} / max {stats['max']}",
            f"- Gruppen < 20: {stats['n_small']}"
            + (f" ({', '.join(stats['small'])})" if stats["small"] else ""),
            "",
        ]
    else:
        lines += ["Keine Industriegruppen-Spalte vorhanden.", ""]
    lines += [
        "## 5. Titel in Fallback je Indikator (Schema B)",
        "",
        "| Indikator | Schema | Titel | Ebene 1 in Zielgruppe | Fallback Sektor | Fallback Global | Ebene 2 entfallen |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in result["fallback_b"]:
        l2 = "–" if r["l2_entfallen"] is None else str(r["l2_entfallen"])
        lines.append(
            f"| {r['indikator']} | {r['schema']} | {r['n']} | {r['ziel']} "
            f"({_fmt(r['ziel'] / r['n'], 0, True) if r['n'] else '–'}) | "
            f"{r['sektor']} | {r['global']} | {l2} |"
        )
    lines.append("")
    warn_b = [
        d for d in result["diagnostics_b"]
        if d.code in ("neut_fallback_share", "industry_not_in_gics_map")
    ]
    if warn_b:
        lines += ["Warnungen (Schema B):", ""]
        lines += [f"- {d.message}" for d in warn_b]
        lines.append("")
    return "\n".join(lines)


def _load_csv_universe(path: str) -> tuple[pd.DataFrame, date]:
    """Koyfin-Export laden und v1-scoren (liefert u. a. Piotroski)."""
    from app.core.data_loader import load_koyfin_csv
    from app.core.scoring import compute_scores
    from app.core.signal_events import snapshot_date_from_universe

    raw = load_koyfin_csv(path)
    scored = compute_scores(raw, STATE.settings)
    return scored, snapshot_date_from_universe(raw, Path(path).name)


def _cmd_compare_schemes(args: argparse.Namespace) -> int:
    scheme_a = args.scheme_a or STATE.settings.v2_neut_scheme_legacy
    scheme_b = args.scheme_b or SCHEME_FROM_SETTINGS
    for scheme in (scheme_a, scheme_b):
        if scheme not in COMPARE_SCHEME_CHOICES:
            print(
                f"Unbekanntes Schema {scheme!r}; zulässig: "
                + ", ".join(COMPARE_SCHEME_CHOICES),
                file=sys.stderr,
            )
            return 2
    if getattr(args, "csv", None):
        STATE.load_from_db()
        universe, snap = _load_csv_universe(args.csv)
        source_note = f"Koyfin-Export `{Path(args.csv).name}`"
    else:
        universe, snap = _load_universe(args.snapshot)
        source_note = (
            f"Archiv-Snapshot {snap.isoformat()}" if args.snapshot else "aktuelles Universum (DB)"
        )
    if universe is None or universe.empty:
        print("Kein Universum verfügbar — Import durchführen, --snapshot oder --csv angeben.",
              file=sys.stderr)
        return 2
    overrides = None
    try:
        overrides = persistence.load_overrides()
    except Exception:  # noqa: BLE001
        overrides = None
    result = compare_schemes(
        universe, STATE.settings, scheme_a, scheme_b, snapshot_date=snap, overrides=overrides
    )
    out_dir = Path(args.out or STATE.settings.risk_report_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"neutralisierung_vergleich_{snap.isoformat()}.md"
    path.write_text(build_neutralization_report(result, source_note), encoding="utf-8")
    print(f"Report geschrieben: {path}")
    print(
        f"Spearman composite_z ({scheme_a} vs. {scheme_b}): {_fmt(result['rho_total'], 3)} · "
        f"Rangänderung > {_fmt(RANK_SHIFT_THRESHOLD, 0)} Punkte: {len(result['movers'])} · "
        f"Top-{TOP_N}-Schnittmenge: {len(result['top_intersection'])}"
    )
    return _exit_code(result["diagnostics_b"])


def _cmd_compare(args: argparse.Namespace) -> int:
    if getattr(args, "scheme_a", None) or getattr(args, "scheme_b", None):
        return _cmd_compare_schemes(args)
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
    p_build.add_argument(
        "--portfolio",
        help=(
            "Bestandsportfolio (ID oder Name eines hochgeladenen Portfolios); "
            "Default: Auswahl der Modellportfolio-Seite, sonst aktives Portfolio"
        ),
    )
    p_build.add_argument("--dry-run", action="store_true")
    p_build.add_argument("--out", help="Report-Verzeichnis (Default: reports/)")
    p_build.set_defaults(func=_cmd_build)

    p_cmp = sub.add_parser(
        "compare",
        help="Rangfolgen v1/v2 oder zwei Neutralisierungsschemata vergleichen",
    )
    p_cmp.add_argument("--v1", action="store_true", help="(Kompatibilität)")
    p_cmp.add_argument("--v2", action="store_true", help="(Kompatibilität)")
    p_cmp.add_argument("--snapshot", help="Archivierter Snapshot (YYYY-MM-DD)")
    p_cmp.add_argument(
        "--scheme-a",
        help="Neutralisierungsschema A (Schema-ID für alle Indikatoren oder "
             "'settings'); Default: v2_neut_scheme_legacy",
    )
    p_cmp.add_argument(
        "--scheme-b",
        help="Neutralisierungsschema B; Default: 'settings' (Zuordnung je "
             "Indikator aus den Einstellungen)",
    )
    p_cmp.add_argument("--csv", help="Koyfin-Export statt DB/Archiv als Snapshot")
    p_cmp.add_argument("--out", help="Report-Verzeichnis (Default: reports/)")
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
