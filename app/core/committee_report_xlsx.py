"""Excel-Anhang des Investment-Komitee-Berichts (openpyxl über pandas).

Alle Tabellen des Builder-Ergebnisses (``committee_report.build_committee_report``)
als Arbeitsmappe: Werte bleiben **numerisch** (Prozent-, Z- und
Score-Zellformate statt vorformatierter Strings), Kopfzeilen deutsch
(``label_for`` + Report-eigene Labels), AutoFilter und fixierte Kopfzeile je
Blatt. Das Dezimalkomma ergibt sich aus der Excel-Locale des Nutzers.
"""

from __future__ import annotations

import io
from datetime import date, datetime

import numpy as np
import pandas as pd

from app.core.committee_report import REPORT_TITLE
from app.core.scoring_v2 import V2_FACTOR_NAMES
from app.ui.formatters import PERCENT_FIELDS, fmt_de
from app.ui.labels import label_for

__all__ = ["REPORT_LABELS", "build_committee_xlsx", "sheet_frames"]

# Blattnamen (≤ 31 Zeichen, keine Sonderzeichen ``[]:*?/\``).
SHEET_OVERVIEW = "Übersicht"
SHEET_UNIVERSE = "Universum"
SHEET_CANDIDATES = "Kandidaten"
SHEET_SELL = "Verkaufen"
SHEET_CHANGES_PREV = "Änderungen Vorimport"
SHEET_CHANGES_MONTH = "Änderungen 1M"
SHEET_ZONE_MATRIX = "Zonenmatrix"
SHEET_SECTORS = "Sektoren"
SHEET_INDUSTRIES = "Industrien"
SHEET_PORTFOLIO = "Portfolio"
SHEET_PORTFOLIO_KPI = "Portfolio-Kennzahlen"
SHEET_PORTFOLIO_EXPOSURE = "Portfolio-Exposure"
SHEET_WATCHLIST = "Watchlist"
SHEET_SIGNALS = "Signalwechsel"
SHEET_MODEL = "Modellportfolio"
SHEET_TRADES = "Trade-Liste"
SHEET_MODEL_EXPOSURE = "MP-Exposure"
SHEET_OVERRIDES = "Overrides"
SHEET_FACTOR_TIMING = "Factor Timing"
SHEET_RISK = "Risiko"
SHEET_AGENTS = "Agenten"
SHEET_DATA_QUALITY = "Datenqualität"
SHEET_PARAMETERS = "Parameter"

REPORT_LABELS: dict[str, str] = {
    "uid": "UID",
    "weight": "Gewicht",
    "weight_current": "Gewicht aktuell",
    "weight_target": "Zielgewicht",
    "weight_model": "Gewicht Modell",
    "weight_effective": "Gewicht effektiv",
    "weight_effective_after_budget": "Gewicht nach Turnover-Budget",
    "delta_w": "Δ Gewicht",
    "delta_prev": "Δ Score vs. Vorimport",
    "delta_month": "Δ Score vs. 1M",
    "delta_score": "Δ Score",
    "delta_pct": "Δ Perzentil",
    "score_now": "Score heute",
    "score_prev": "Score Vorsnapshot",
    "score_month": "Score ~1M",
    "zone_now": "Zone heute",
    "zone_prev": "Zone Vorsnapshot",
    "zone_month": "Zone ~1M",
    "class_now": "Klasse heute",
    "class_prev": "Klasse Vorsnapshot",
    "zone_at_run": "Zone beim Lauf",
    "zone_drift": "Zonenwechsel seit Lauf",
    "composite_z_at_run": "Composite Z beim Lauf",
    "composite_pct_at_run": "Perzentil beim Lauf",
    "composite_score_now": "Composite heute",
    "zone_v2_now": "Zone heute",
    "classification_v2_now": "Klasse heute",
    "total_score_now": "Score heute",
    "recommendation_now": "Empfehlung heute",
    "status": "Status",
    "flags": "Flags",
    "severity": "Dringlichkeit",
    "filter_reasons": "Filtergründe",
    "in_portfolio": "Im Portfolio",
    "in_universe": "Im Universum",
    "is_new": "Signal neu",
    "state_since": "Signal seit",
    "days_in_state": "Tage im Zustand",
    "momentum_prev": "Signal vorher",
    "momentum_now": "Signal heute",
    "momentum": "Signal heute",
    "agent_rating": "Agenten-Rating",
    "agent_rated_at": "Agenten-Rating vom",
    "reason": "Grund",
    "watch_reason": "Beobachtungsgrund",
    "action": "Aktion",
    "action_saved": "Aktion (gespeichert)",
    "override_id": "Override",
    "cte": "cTE",
    "rrg": "Rotations-Quadrant",
    "rrg_prev": "Quadrant ~1M",
    "rrg_changed": "Quadrantenwechsel",
    "n": "Anzahl",
    "n_eligible": "Eligible",
    "n_candidates": "Kandidaten",
    "share_candidates": "Kandidatenanteil",
    "n_sell": "Verkaufen",
    "avg_score": "Ø Score",
    "score": "Score",
    "delta_history_1m": "Δ Score (Historie ~1M)",
    "mom_12_1": "Momentum 12M−1M (pp)",
    "sma50_dist": "Distanz SMA-50 (pp)",
    "sma200_dist": "Distanz SMA-200 (pp)",
    "breadth_sma200": "Breadth > SMA-200 (%)",
    "breadth_golden": "Breadth Golden Cross (%)",
    "pf_weight": "Gewicht Portfolio",
    "bm_weight": "Gewicht Benchmark",
    "active_weight": "Aktives Gewicht",
    "low_confidence": "Geringe Konfidenz",
    "confidence_reasons": "Konfidenz-Hinweise",
    "industry": "Industrie",
    "name": "Name",
    "portfolio": "Portfolio",
    "benchmark": "Benchmark",
    "active": "aktiv",
    "band_breach": "Bandverletzung",
    "model": "Modell",
    "current": "Bestand",
    "delta": "Δ",
    "factor": "Faktor",
    "pf_weighted": "Portfolio (gewichtet)",
    "pf_mean": "Portfolio (Ø)",
    "universe_mean": "Universum (Ø)",
    "candidates_mean": "Kandidaten (Ø)",
    "strategic": "Strategisch",
    "tactical": "Taktisch",
    "tilt": "Tilt",
    "regime": "Regime",
    "date": "Datum",
    "id": "ID",
    "direction": "Richtung",
    "target_weight": "Zielgewicht",
    "owner": "Verantwortlich",
    "expires_at": "Läuft ab",
    "days_left": "Resttage",
    "due_soon": "Bald fällig",
    "rating": "Rating",
    "tone": "Tonalität",
    "created_at": "Erstellt",
    "age_days": "Alter (Tage)",
    "stale": "Veraltet",
    "conflict": "Konflikt Quant/Agent",
    "label": "Parameter",
    "value": "Wert",
    "group": "Gruppe",
    "key": "Schlüssel",
    "global": "Global",
    "profile": "Profil",
    "code": "Code",
    "message": "Meldung",
    "text": "Aussage",
    "section": "Bereich",
    "nr": "Nr.",
    "kennzahl": "Kennzahl",
    "wert": "Wert",
    "ticker": "Ticker",
    "gewicht": "Gewicht",
    "mcte": "MCTE",
    "cte_bp": "cTE (bp)",
    "sektor": "Sektor",
    "szenario": "Szenario",
    "pf_pnl": "Portfolio P&L",
    "bm_pnl": "Benchmark P&L",
    "aktiv": "aktiv",
    "abdeckung": "Abdeckung",
    "n_geringe_guete": "Titel mit geringer Güte",
    "start": "Start",
    "ende": "Ende",
    "coverage": "Abdeckung",
    "belastbar": "Belastbar",
    "pf_rendite": "Portfolio-Rendite",
    "bm_rendite": "Benchmark-Rendite",
    "max_drawdown": "Max. Drawdown",
    "zone_v2": "Zone",
    "composite_score": "Composite (v2)",
    "composite_z": "Composite Z",
    "composite_pct": "Perzentil",
    "classification_v2": "Klasse (v2)",
    "data_coverage_v2": "Datenabdeckung (v2)",
    "trend_warning": "Death Cross",
    "filter_pass": "Filter bestanden",
    "cov_value": "Abdeckung Value",
    "cov_quality": "Abdeckung Quality",
    "cov_momentum": "Abdeckung Momentum",
    "cov_investment": "Abdeckung Investment",
    "z_value": "Z Value",
    "z_quality": "Z Quality",
    "z_momentum": "Z Momentum",
    "z_investment": "Z Investment",
    "dz_value": "Δ Z Value",
    "dz_quality": "Δ Z Quality",
    "dz_momentum": "Δ Z Momentum",
    "dz_investment": "Δ Z Investment",
}

# Spalten als Prozent (Dezimalanteil im Frame).
_PERCENT_COLS: set[str] = set(PERCENT_FIELDS) | {
    "weight", "pf_weight", "bm_weight", "active_weight", "share_candidates",
    "portfolio", "benchmark", "active", "model", "current", "delta",
    "strategic", "tactical", "tilt", "target_weight", "gewicht", "coverage",
    "pf_rendite", "bm_rendite", "aktiv", "max_drawdown", "pf_pnl", "bm_pnl",
    "abdeckung", "delta_pct", "composite_pct_at_run", "mcte", "cte",
}
_TWO_DEC_COLS: set[str] = {
    "composite_z", "composite_z_at_run", "altman_z", "beta", "pe", "pb", "ps",
    "pfcf", "ev_ebitda", "peg", "debt_equity", "int_coverage", "current_ratio",
    "ocf_ni", "value_spread", "spearman_rho",
} | {f"z_{f}" for f in V2_FACTOR_NAMES} | {f"dz_{f}" for f in V2_FACTOR_NAMES}
_ONE_DEC_COLS: set[str] = {
    "composite_score", "total_score", "score", "score_now", "score_prev",
    "score_month", "delta_score", "delta_prev", "delta_month", "avg_score",
    "delta_history_1m", "mom_12_1", "sma50_dist", "sma200_dist",
    "breadth_sma200", "breadth_golden", "composite_score_now",
    "total_score_now", "value_score", "quality_score", "growth_score",
    "momentum_score", "lowvol_score", "cte_bp",
}
_INT_COLS: set[str] = {"piotroski", "n", "n_eligible", "n_candidates", "n_sell", "days_in_state", "age_days", "days_left", "id", "nr", "n_geringe_guete", "override_id"}
_DATE_COLS: set[str] = {"state_since", "expires_at", "date", "start", "ende", "snapshot_date", "run_date", "created_at", "agent_rated_at"}

FMT_PERCENT = "0.0 %"
FMT_TWO = "0.00"
FMT_ONE = "0.0"
FMT_INT = "0"
FMT_MCAP = '#,##0 "Mio."'
FMT_DATE = "DD.MM.YYYY"


def _header(col: str) -> str:
    if col in REPORT_LABELS:
        return REPORT_LABELS[col]
    return label_for(col)


def _cell_value(v):
    """Listen/Dicts → Text, pandas-NA → None, Timestamps → datetime."""
    if isinstance(v, (list, tuple)):
        return " · ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    if v is None:
        return None
    if isinstance(v, float) and pd.isna(v):
        return None
    if isinstance(v, pd.Timestamp):
        return v.to_pydatetime()
    if isinstance(v, (bool, np.bool_)):
        return "ja" if v else "nein"
    if isinstance(v, np.generic):
        return v.item()
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


def _frame(rows: list[dict] | pd.DataFrame | None, columns: list[str] | None = None) -> pd.DataFrame:
    if rows is None:
        return pd.DataFrame()
    df = rows.copy() if isinstance(rows, pd.DataFrame) else pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=columns or list(df.columns))
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    return df


def _kv_frame(pairs: list[tuple[str, object]]) -> pd.DataFrame:
    return pd.DataFrame({"kennzahl": [p[0] for p in pairs], "wert": [_cell_value(p[1]) for p in pairs]})


def _d(value) -> object:
    """Datum als Zelle (date/datetime bleiben, sonst Text)."""
    if isinstance(value, (date, datetime)):
        return value
    return value if value is not None else "–"


# ── Blätter ────────────────────────────────────────────────────────────────


def sheet_frames(report: dict) -> list[tuple[str, list[tuple[str | None, pd.DataFrame]]]]:
    """Blattname → Liste von (Untertitel, Frame)-Blöcken, die untereinander
    geschrieben werden. Optionale Abschnitte nur bei ``available``."""
    meta = report["meta"]
    cols = meta["cols"]
    comp = meta["comparison"]
    sheets: list[tuple[str, list[tuple[str | None, pd.DataFrame]]]] = []

    # Übersicht
    uni = report["universe"]
    pf = report["portfolio"]
    overview = [
        ("Bericht", REPORT_TITLE),
        ("Stand (Export-Datum)", _d(meta["asof"])),
        ("Erstellt am", meta["generated_at"]),
        ("Erstellt von", meta.get("prepared_by") or "–"),
        ("Portfolio", meta["portfolio_name"]),
        ("Scoring-Version", meta["scoring_version"]),
        ("Vergleich Vorimport", _d(comp.get("prev")) if comp.get("prev") else comp.get("prev_note")),
        ("Vergleich ~1 Monat", _d(comp.get("month")) if comp.get("month") and not comp.get("same_as_prev") else comp.get("month_note")),
        ("Universum (Titel)", meta["n_universe"]),
        ("Settings-Hash", meta["settings_hash"]),
        ("Neutralisierungs-Hash", meta["neut_scheme_hash"]),
    ]
    findings = pd.DataFrame(
        [{"nr": i + 1, "tone": f["tone"], "section": f["section"], "text": f["text"]} for i, f in enumerate(report.get("key_findings", []))]
    )
    kpis = []
    if uni.get("available"):
        kpis += [(f"Universum: {k}", v) for k, v in uni["zone_counts"].items()]
        kpis += [("Universum: eligible", uni["n_eligible"]), ("Universum: Ø Score", uni["avg_score"]), ("Universum: Median Score", uni["median_score"])]
    if pf.get("available"):
        kpis += [
            ("Portfolio: gewichteter Score", pf["weighted_avg_score"]),
            ("Portfolio: Δ Score vs. Vorimport", pf.get("delta_weighted_prev")),
            ("Portfolio: Δ Score vs. 1M", pf.get("delta_weighted_month")),
            ("Portfolio: Positionen mit Flags", pf["n_flagged"]),
            ("Portfolio: Positionen in FILTER", pf["n_filter_fail"]),
            ("Portfolio: Positionen in VERKAUFEN", pf["n_sell_zone"]),
        ]
    sheets.append((SHEET_OVERVIEW, [("Metadaten", _kv_frame(overview)), ("Key Findings", findings), ("Kennzahlen", _kv_frame(kpis))]))

    # Universum / Kandidaten / Verkaufen
    if uni.get("available"):
        sheets.append((SHEET_UNIVERSE, [(None, _frame(uni["all_stocks"], uni["stock_columns"]))]))
        sheets.append((SHEET_CANDIDATES, [(None, _frame(uni["all_candidates"], uni["stock_columns"]))]))
        sheets.append((SHEET_SELL, [(None, _frame(uni["sell_zone"], uni["stock_columns"]))]))

    # Änderungen
    ch = report["changes"]
    for key, sheet in (("vs_prev", SHEET_CHANGES_PREV), ("vs_month", SHEET_CHANGES_MONTH)):
        block = ch.get(key) or {}
        if not block.get("available"):
            continue
        deltas = block["deltas"]
        blocks: list[tuple[str | None, pd.DataFrame]] = [
            (
                f"Vergleich mit Snapshot {block['snapshot_date'].strftime('%d.%m.%Y')} · "
                f"Spearman ρ {fmt_de(block.get('spearman_rho'), 2)} · "
                f"Zonenwechsel {block.get('n_zone_changed')}",
                _frame(deltas, block["delta_columns"]),
            ),
        ]
        sheets.append((sheet, blocks))
    matrices: list[tuple[str | None, pd.DataFrame]] = []
    for key, label in (("vs_prev", "Vorimport"), ("vs_month", "~1 Monat")):
        block = ch.get(key) or {}
        if block.get("available"):
            m = block["zone_matrix"].copy()
            m.insert(0, "Zone vorher \\ heute", m.index)
            matrices.append((f"Zonenmatrix vs. {label} ({block['snapshot_date'].strftime('%d.%m.%Y')})", m.reset_index(drop=True)))
    if matrices:
        sheets.append((SHEET_ZONE_MATRIX, matrices))

    # Sektoren
    sec = report["sectors"]
    if sec.get("available"):
        sector_cols = ["sector", "n", "score", "score_prev", "delta_prev", "score_month", "delta_month", "ret_1m", "ret_3m", "ret_6m", "ret_12m", "mom_12_1", "sma50_dist", "sma200_dist", "breadth_sma200", "breadth_golden", "rrg", "rrg_prev", "rrg_changed", "n_candidates", "share_candidates", "n_sell", "pf_weight", "bm_weight", "active_weight", "low_confidence", "confidence_reasons"]
        sdf = _frame(sec["rows"], sector_cols)
        # Sektor-Returns liegen in Prozentpunkten (aggregate_sectors) → keine Prozentformatierung.
        sheets.append((SHEET_SECTORS, [(None, sdf)]))
        ind_rows = [
            {"sector": r["sector"], **i} for r in sec["rows"] for i in (r.get("industries") or [])
        ]
        if ind_rows:
            sheets.append((SHEET_INDUSTRIES, [(None, _frame(ind_rows, ["sector", "industry", "n", "score", "delta_score", "mom_12_1", "sma200_dist", "breadth_sma200"]))]))

    # Portfolio
    if pf.get("available"):
        pos = _frame(pf["positions"], pf["position_columns"])
        blocks = [(f"Portfolio „{pf['name']}“ · {pf['n_resolved']} von {pf['n_positions']} Positionen im Universum", pos)]
        extras = []
        if pf.get("missing"):
            extras.append(("Nicht im Universum", pd.DataFrame({"ticker": pf["missing"]})))
        if pf.get("ambiguous"):
            extras.append(("Mehrdeutig (Ticker-Kollision)", pd.DataFrame({"ticker": pf["ambiguous"]})))
        sheets.append((SHEET_PORTFOLIO, blocks + extras))
        conc = pf["concentration"]
        kpi_pairs = [
            ("Gewichteter Score", pf["weighted_avg_score"]),
            ("Ø Score (gleichgewichtet)", pf["equal_avg_score"]),
            ("Gewichteter Score Vorimport", pf.get("weighted_avg_score_prev")),
            ("Gewichteter Score ~1M", pf.get("weighted_avg_score_month")),
            ("Universum Ø Score", pf["universe_avg_score"]),
            ("Universum Median Score", pf["universe_median_score"]),
            ("Positionen", pf["n_positions"]),
            ("Davon im Universum", pf["n_resolved"]),
            ("Gewichte aus Upload", pf["has_weights"]),
            ("Summe aufgelöster Gewichte", pf["weight_resolved"]),
            ("Positionen mit Flags", pf["n_flagged"]),
            ("Death Cross", pf["n_trend_warning"]),
            ("Signalwechsel seit Vorimport", pf["n_signal_new"]),
            ("Top-5-Gewicht", conc.get("top5_weight")),
            ("Top-10-Gewicht", conc.get("top10_weight")),
            ("Max. Einzelgewicht", conc.get("max_weight")),
            ("HHI", conc.get("hhi")),
            ("Effektive Titelzahl", conc.get("effective_n")),
            ("Positionen über Cap", conc.get("n_above_cap")),
            ("Gewichts-Cap", conc.get("cap")),
        ]
        zone_df = pd.DataFrame(
            [{"zone": z, "n": pf["zone_counts"].get(z, 0), "weight": pf["zone_weights"].get(z, 0.0)} for z in cols["zone_values"]]
        )
        fx = _frame(pf["factor_exposure"], ["factor", "pf_weighted", "pf_mean", "universe_mean", "candidates_mean"])
        sheets.append((SHEET_PORTFOLIO_KPI, [("Kennzahlen", _kv_frame(kpi_pairs)), ("Zonen", zone_df), ("Faktor-Exposure (Ø Z)", fx)]))
        sheets.append((SHEET_PORTFOLIO_EXPOSURE, [
            (f"Sektoren (Band ± {fmt_de(pf['sector_band'] * 100, 0)} %)", _frame(pf["sector_exposure"], ["name", "portfolio", "benchmark", "active", "band_breach"])),
            (f"Regionen (Band ± {fmt_de(pf['region_band'] * 100, 0)} %)", _frame(pf["region_exposure"], ["name", "portfolio", "benchmark", "active", "band_breach"])),
        ]))
    # Watchlist & Signalwechsel
    watch_blocks: list[tuple[str | None, pd.DataFrame]] = []
    entry = ch.get("watchlist_entry") or []
    if entry:
        watch_blocks.append(("Kandidaten in spe (HALTEN knapp unter Einstiegsschwelle)", _frame(entry)))
    if pf.get("available") and pf.get("watchlist_exit"):
        watch_blocks.append(("Verkaufsgefährdete Positionen (knapp über Ausstiegsschwelle)", _frame(pf["watchlist_exit"])))
    if pf.get("available") and pf.get("immediate_actions"):
        watch_blocks.append(("Sofortmaßnahmen (Portfolio in FILTER/VERKAUFEN)", _frame(pf["immediate_actions"])))
    if watch_blocks:
        sheets.append((SHEET_WATCHLIST, watch_blocks))
    sig_blocks: list[tuple[str | None, pd.DataFrame]] = []
    if pf.get("available") and pf.get("signal_changes"):
        sig_blocks.append(("Portfolio", _frame(pf["signal_changes"])))
    if ch.get("candidate_signal_changes"):
        sig_blocks.append(("Kandidaten (Golden/Death Cross neu)", _frame(ch["candidate_signal_changes"])))
    if sig_blocks:
        sheets.append((SHEET_SIGNALS, sig_blocks))

    # Modellportfolio
    mp = report["model_portfolio"]
    if mp.get("available"):
        meta_pairs = [
            ("Lauf vom", _d(mp["run_date"])),
            ("Alter (Tage)", mp.get("age_days")),
            ("Rebalance-Modus", mp["rebalance_mode"]),
            ("Titel", mp["n_titles"]),
            ("TE ex-ante", mp.get("te_ex_ante")),
            ("Kursabdeckung TE", mp.get("te_coverage")),
            ("Turnover (rekonstruiert)", mp["turnover_oneway"]),
            ("Turnover (gespeichert)", mp.get("turnover_saved")),
            ("Käufe", mp["n_buy"]), ("Verkäufe", mp["n_sell"]), ("Aufstocken", mp["n_increase"]), ("Reduzieren", mp["n_reduce"]), ("Verschoben", mp["n_deferred"]),
            ("Bestandsportfolio", mp["source_portfolio_name"]),
            ("Konstruktionsprofil", mp.get("pc_profile_name") or "Standard"),
            ("Settings-Hash (Lauf)", mp["settings_hash"]),
            ("Settings-Hash (aktuell)", mp["settings_hash_current"]),
            ("Settings unverändert", mp["settings_hash_matches"]),
            ("Zieltitel mit Zonenwechsel seit Lauf", mp["n_zone_drift"]),
        ]
        sheets.append((SHEET_MODEL, [("Lauf", _kv_frame(meta_pairs)), ("Zielportfolio", _frame(mp["portfolio"], mp["portfolio_columns"]))]))
        sheets.append((SHEET_TRADES, [(None, _frame(mp["all_trades"], mp["trade_columns"]))]))
        sheets.append((SHEET_MODEL_EXPOSURE, [
            (f"Sektoren (Band ± {fmt_de(mp['sector_band'] * 100, 0)} %)", _frame(mp["sector_exposure"], ["name", "portfolio", "benchmark", "active", "band_breach"])),
            ("Modell vs. Bestand (Sektoren)", _frame(mp["vs_active"], ["name", "model", "current", "delta"])),
            (f"Regionen (Band ± {fmt_de(mp['region_band'] * 100, 0)} %)", _frame(mp["region_exposure"], ["name", "portfolio", "benchmark", "active", "band_breach"])),
            ("Faktor-Exposure (Ø Z)", _frame(mp["factor_exposure"], ["factor", "pf_weighted", "pf_mean", "universe_mean", "candidates_mean"])),
        ]))
        if mp.get("overrides"):
            sheets.append((SHEET_OVERRIDES, [(None, _frame(mp["overrides"]))]))

    # Factor Timing
    ft = report["factor_timing"]
    if ft.get("available"):
        sheets.append((SHEET_FACTOR_TIMING, [
            ("Regime", _kv_frame([("Regime", ft["regime"]), ("Regime seit", _d(ft["regime_since"])), ("Letzte Entscheidung", _d(ft["regime_date"])), ("Modus", ft["mode"]), ("Value-Spread (P/E Top-Quintil / Universum)", ft.get("value_spread"))])),
            ("Gewichte", _frame(ft["weights"], ["factor", "strategic", "tactical", "tilt"])),
            ("Eingaben", _kv_frame(list((ft.get("inputs") or {}).items()))),
            ("Faktor-Momentum aus Universum (pp)", _kv_frame(list((ft.get("momentum_proxies") or {}).items()))),
            ("Regime-Verlauf", _frame(ft["timeline"], ["date", "regime"])),
        ]))

    # Risiko
    rk = report["risk"]
    if rk.get("available"):
        ex = rk.get("expost") or {}
        sheets.append((SHEET_RISK, [
            ("Ex-post vs. Benchmark " + str(rk.get("benchmark")), _kv_frame([(k, v) for k, v in ex.items()] + [("TE ex-ante (Ledoit-Wolf)", rk.get("te_ex_ante")), ("TE ex-ante (Sample)", rk.get("te_sample"))])),
            ("MCTE-Ranking (Top 10)", _frame(rk["ranking"])),
            ("Sektor-CTE", _frame(rk["sector_cte"])),
            ("Szenarien", _frame(rk["scenarios"])),
            ("Faktor-Schocks", _frame(rk["shocks"])),
            ("Datenqualität", _kv_frame([("Nicht auflösbar", ", ".join(rk["unresolved"]) or "–"), ("Ohne Kurscache", ", ".join(rk["missing_cache"]) or "–")])),
        ]))

    # Agenten
    ag = report["agents"]
    if ag.get("available"):
        sheets.append((SHEET_AGENTS, [(None, _frame(ag["rows"])), ("Konflikte Quant/Agent", _frame(ag["conflicts"]))]))

    # Datenqualität
    dq = report["data_quality"]
    if dq.get("available"):
        pairs = [
            ("Stand", _d(dq["asof"])), ("Import-Alter (Tage)", dq["import_age_days"]), ("Veraltet", dq["stale"]),
            ("Universum", dq["n_universe"]), ("Vorimport (Titel)", dq.get("n_prev")), ("~1M (Titel)", dq.get("n_month")),
            ("Archiv-Snapshots", dq["n_snapshots"]), ("Ältester Snapshot", _d(dq.get("oldest_snapshot"))),
            ("Ø Datenabdeckung", dq.get("avg_coverage")), ("Median Datenabdeckung", dq.get("median_coverage")),
            ("Anteil unter Mindestabdeckung", dq.get("share_below_min_coverage")),
            ("Ticker-Duplikate", dq["duplicate_tickers"]),
            ("Fehlende Pflichtspalten", ", ".join(dq["missing_required_columns"]) or "–"),
            ("Neutralisierungs-Hash", dq["neut_scheme_hash"]),
            ("Schema-Drift zu Snapshots", ", ".join(dq["neut_scheme_drift"]) or "–"),
            ("Live-Scoring entspricht Einstellungen", dq["live_scheme_matches_settings"]),
            ("Signal-Historie (Snapshots)", dq["n_signal_snapshots"]),
            ("Risiko-Kurscache", dq["risk_cache"]["available"]),
            ("Risiko-Cache Stand", _d(dq["risk_cache"].get("last_refreshed"))),
            ("Portfolio-Ticker nicht im Universum", ", ".join(dq["portfolio_missing"]) or "–"),
            ("Portfolio-Ticker mehrdeutig", ", ".join(dq["portfolio_ambiguous"]) or "–"),
        ] + [(f"Titel ohne {k}", v) for k, v in dq["missing_fields"].items()] + [
            (f"Abdeckung {f}", v) for f, v in (dq.get("factor_coverage") or {}).items()
        ] + [(f"Optionale Spalte {k}", v) for k, v in dq["optional_columns"].items()]
        diag_df = pd.DataFrame(
            [{"severity": d.severity, "code": d.code, "uid": d.uid or "", "message": d.message} for d in dq["diagnostics"]]
        )
        report_diag = pd.DataFrame(
            [{"severity": d.severity, "code": d.code, "uid": d.uid or "", "message": d.message} for d in report.get("diagnostics", [])]
        )
        sheets.append((SHEET_DATA_QUALITY, [("Kennzahlen", _kv_frame(pairs)), ("Diagnosen Scoring-Lauf", diag_df), ("Diagnosen Bericht", report_diag)]))

    # Parameter
    pr = report["parameters"]
    if pr.get("available"):
        rows = []
        for g in pr["groups"]:
            for r in g["rows"]:
                rows.append({"group": g["title"], "label": r["label"], "value": _cell_value(r["value"]), "key": r["key"]})
        blocks = [
            ("Hashes", _kv_frame([("Settings-Hash", pr["settings_hash"]), ("Neutralisierungs-Hash", pr["neut_scheme_hash"]), ("Konstruktionsprofil", pr.get("pc_profile_name") or "Standard")])),
            ("Parameter", pd.DataFrame(rows)),
        ]
        if pr.get("profile_differences"):
            blocks.append(("Abweichungen Profil vs. global", _frame(pr["profile_differences"], ["label", "global", "profile", "key"])))
        sheets.append((SHEET_PARAMETERS, blocks))
    return sheets


# ── Schreiben ──────────────────────────────────────────────────────────────


def _number_format(col: str) -> str | None:
    if col == "market_cap":
        return FMT_MCAP
    if col in _DATE_COLS:
        return FMT_DATE
    if col in _PERCENT_COLS:
        return FMT_PERCENT
    if col in _INT_COLS:
        return FMT_INT
    if col in _TWO_DEC_COLS:
        return FMT_TWO
    if col in _ONE_DEC_COLS:
        return FMT_ONE
    return None


def _write_block(ws, df: pd.DataFrame, start_row: int, title: str | None, bold_font, fill) -> int:
    """Schreibt Titel + Tabelle ab ``start_row`` (1-basiert); liefert die
    nächste freie Zeile."""
    from openpyxl.utils import get_column_letter

    row = start_row
    if title:
        ws.cell(row=row, column=1, value=title).font = bold_font
        row += 1
    if df is None or df.empty:
        ws.cell(row=row, column=1, value="(keine Einträge)")
        return row + 2
    columns = list(df.columns)
    for j, col in enumerate(columns, start=1):
        c = ws.cell(row=row, column=j, value=_header(str(col)))
        c.font = bold_font
        c.fill = fill
    header_row = row
    row += 1
    for _, rec in df.iterrows():
        for j, col in enumerate(columns, start=1):
            c = ws.cell(row=row, column=j, value=_cell_value(rec[col]))
            fmt = _number_format(str(col))
            if fmt and isinstance(c.value, (int, float, date, datetime)) and not isinstance(c.value, bool):
                c.number_format = fmt
        row += 1
    last_row = row - 1
    if ws.auto_filter.ref is None or ws.auto_filter.ref == "":
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(columns))}{last_row}"
    for j, col in enumerate(columns, start=1):
        width = max(10, min(48, int(max([len(_header(str(col)))] + [len(str(_cell_value(v) or "")) for v in df[col].head(200)]) * 1.1) + 2))
        letter = get_column_letter(j)
        if (ws.column_dimensions[letter].width or 0) < width:
            ws.column_dimensions[letter].width = width
    return row + 1


def build_committee_xlsx(report: dict) -> bytes:
    """Arbeitsmappe (xlsx) aus dem Builder-Ergebnis; liefert die Bytes."""
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    wb.remove(wb.active)
    bold = Font(bold=True)
    fill = PatternFill("solid", fgColor="F6F2EA")
    for sheet, blocks in sheet_frames(report):
        ws = wb.create_sheet(title=sheet[:31])
        row = 1
        first = True
        for title, df in blocks:
            row = _write_block(ws, df, row, title, bold, fill)
            if first and (df is not None and not df.empty):
                ws.freeze_panes = "A3" if title else "A2"
                first = False
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
