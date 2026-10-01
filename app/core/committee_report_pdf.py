"""PDF/HTML-Renderer des Investment-Komitee-Berichts.

``build_pdf_context`` formatiert das Builder-Ergebnis (Rohwerte) in
deutsche Strings (Dezimalkomma, Prozent, Datum) und kürzt lange Tabellen auf
PDF-taugliche Längen (vollständige Listen liegen im Excel-Anhang).
``render_committee_pdf`` rendert ``committee.html.j2`` über denselben
WeasyPrint-Worker wie Factsheet und Agenten-Berichte; fehlt WeasyPrint,
liefert ``render_committee_fallback_html`` dieselbe Seite als eigenständige
HTML-Datei (CSS inline, ohne ``@page``/``running()``), die im Browser
geöffnet werden kann.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Callable

from app.core.committee_report import (
    AGENT_RATING_TONE,
    OVERRIDE_DUE_DAYS,
    REPORT_TITLE,
    RRG_IMPROVING,
    RRG_LAGGING,
    RRG_LEADING,
    RRG_WEAKENING,
    V2_FACTOR_NAMES,
)
from app.core.factsheet_pdf import (
    FactsheetRenderError,
    _TEMPLATE_DIR,
    _jinja_env,
    _run_weasyprint,
)
from app.core.pdf_export import CONFIDENTIALITY, PDF_AUTHOR
from app.ui.formatters import fmt_de, fmt_int, fmt_percent, fmt_signed_percent

__all__ = [
    "DISCLAIMER",
    "FactsheetRenderError",
    "PDF_MAX_ROWS",
    "PDF_MAX_POSITIONS",
    "build_pdf_context",
    "committee_filename",
    "render_committee_fallback_html",
    "render_committee_html",
    "render_committee_pdf",
]

DISCLAIMER = (
    "Interne Entscheidungsunterstützung · keine Anlageberatung im Sinne des "
    "WpHG · Meeder & Seifer Family Office GmbH"
)
PDF_MAX_ROWS = 25
PDF_MAX_POSITIONS = 40
PDF_MAX_TRADES = 40
TOP_LIMIT = 10
TRUNCATED_NOTE = "Vollständige Liste im Excel-Anhang."

_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
    "August", "September", "Oktober", "November", "Dezember",
)

_TONE_ICON = {"up": "▲", "down": "▼", "warn": "◆", "info": "●"}
_ZONE_CLASS = {"KANDIDAT": "is-up", "HALTEN": "is-warn", "VERKAUFEN": "is-down", "FILTER": "is-muted"}
_ACTION_CLASS = {"KAUF": "is-up", "AUFSTOCKEN": "is-up", "VERKAUF": "is-down", "REDUZIEREN": "is-down", "VERSCHOBEN": "is-warn", "HALTEN": "is-muted"}
_RRG_CLASS = {RRG_LEADING: "is-up", RRG_IMPROVING: "is-gold", RRG_WEAKENING: "is-warn", RRG_LAGGING: "is-down"}


# ── Formatierung ───────────────────────────────────────────────────────────


def _is_missing(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, float):
        return v != v  # NaN
    try:
        import pandas as pd

        return bool(pd.isna(v)) if not isinstance(v, (list, tuple, dict, str)) else False
    except (TypeError, ValueError):
        return False


def _s(v: Any) -> str:
    return "–" if _is_missing(v) or v == "" else str(v)


def _n(v: Any, decimals: int = 1) -> str:
    return "–" if _is_missing(v) else fmt_de(v, decimals)


def _signed(v: Any, decimals: int = 1, suffix: str = "") -> str:
    if _is_missing(v):
        return "–"
    f = float(v)
    sign = "+" if f > 0 else ("−" if f < 0 else "±")
    return f"{sign}{fmt_de(abs(f), decimals)}{suffix}"


def _pct(v: Any, decimals: int = 1) -> str:
    return "–" if _is_missing(v) else fmt_percent(v, decimals)


def _spct(v: Any, decimals: int = 1) -> str:
    return "–" if _is_missing(v) else fmt_signed_percent(v, decimals)


def _pp(v: Any, decimals: int = 1) -> str:
    """Prozentpunkte (Dezimalanteil) mit Vorzeichen."""
    return "–" if _is_missing(v) else _signed(float(v) * 100, decimals, " pp")


def _int(v: Any) -> str:
    return "–" if _is_missing(v) else fmt_int(v)


def _date(v: Any) -> str:
    if isinstance(v, (date, datetime)):
        return v.strftime("%d.%m.%Y")
    if _is_missing(v):
        return "–"
    try:
        import pandas as pd

        return pd.Timestamp(v).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(v)


def _date_long(v: date | datetime) -> str:
    return f"{v.day}. {_MONTHS_DE[v.month - 1]} {v.year}"


def _list(v: Any) -> str:
    if isinstance(v, (list, tuple)):
        return " · ".join(str(x) for x in v) or "–"
    return _s(v)


def _bool(v: Any) -> str:
    if _is_missing(v):
        return "–"
    return "ja" if bool(v) else "nein"


def _sign_class(v: Any) -> str:
    if _is_missing(v):
        return ""
    f = float(v)
    return "is-up" if f > 0 else ("is-down" if f < 0 else "")


# ── Tabellen ───────────────────────────────────────────────────────────────

# Spalten-Spezifikation: (key, label, formatter, align, class_fn)
Spec = tuple[str, str, Callable[[Any], str], str, Callable[[Any], str] | None]


def _col(key: str, label: str, fmt: Callable[[Any], str] = _s, align: str = "l", cls: Callable[[Any], str] | None = None) -> Spec:
    return (key, label, fmt, align, cls)


def _table(rows: list[dict], spec: list[Spec], *, limit: int | None = None, empty: str = "Keine Einträge.") -> dict:
    total = len(rows)
    shown = rows[:limit] if limit else rows
    out_rows = []
    for r in shown:
        cells = []
        for key, _label, fmt, align, cls in spec:
            v = r.get(key)
            cells.append({"text": fmt(v), "align": align, "cls": (cls(v) if cls else "") or ""})
        out_rows.append(cells)
    return {
        "headers": [{"text": label, "align": align} for _k, label, _f, align, _c in spec],
        "rows": out_rows,
        "empty": empty,
        "truncated": bool(limit and total > limit),
        "total": total,
        "shown": len(out_rows),
        "note": (f"{len(out_rows)} von {total} Zeilen gezeigt. {TRUNCATED_NOTE}" if limit and total > limit else ""),
    }


def _zone_cls(v: Any) -> str:
    return _ZONE_CLASS.get(str(v), "")


def _action_cls(v: Any) -> str:
    return _ACTION_CLASS.get(str(v), "")


def _rrg_cls(v: Any) -> str:
    return _RRG_CLASS.get(str(v), "")


def _bool_cls(v: Any) -> str:
    return "is-down" if (not _is_missing(v) and bool(v)) else ""


# ── Kontext ────────────────────────────────────────────────────────────────


def _kpi(label: str, value: str, sub: str = "", tone: str = "") -> dict:
    return {"label": label, "value": value, "sub": sub, "tone": tone}


def _universe_ctx(report: dict, cols: dict, comp: dict) -> dict:
    uni = report["universe"]
    if not uni.get("available"):
        return {"available": False, "note": uni.get("note", "")}
    n = uni["n"] or 1
    zones = [
        {"zone": z, "n": uni["zone_counts"].get(z, 0), "share": _pct(uni["zone_counts"].get(z, 0) / n, 0), "width": round(100 * uni["zone_counts"].get(z, 0) / n, 1), "cls": _zone_cls(z)}
        for z in cols["zone_values"]
    ]
    klass = [
        {"klass": k, "n": v, "width": round(100 * v / n, 1)}
        for k, v in uni["class_counts"].items()
    ]
    stock_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col(cols["score"], "Score", _n, "r"), _col(cols["klass"], "Kl."),
    ]
    if cols["z"]:
        stock_spec.append(_col(cols["z"], "Z", lambda v: _n(v, 2), "r"))
        for f in V2_FACTOR_NAMES:
            stock_spec.append(_col(f"z_{f}", f"Z {f[:3].capitalize()}", lambda v: _n(v, 2), "r", _sign_class))
    stock_spec += [
        _col("sma_signal", "SMA"),
        _col("delta_prev", "Δ Vorimp.", _signed, "r", _sign_class),
    ]
    if not comp.get("same_as_prev"):
        stock_spec.append(_col("delta_month", "Δ 1M", _signed, "r", _sign_class))
    stock_spec.append(_col("in_portfolio", "PF", lambda v: "●" if v else "", "c"))
    sector_spec = [
        _col("sector", "Sektor"), _col("n", "n", _int, "r"), _col("n_eligible", "eligible", _int, "r"),
        _col("n_candidates", "Kandidaten", _int, "r"), _col("share_candidates", "Anteil", lambda v: _pct(v, 0), "r"),
        _col("n_sell", "Verkaufen", _int, "r"), _col("avg_score", "Ø Score", _n, "r"),
    ]
    return {
        "available": True,
        "n": _int(uni["n"]),
        "n_eligible": _int(uni["n_eligible"]),
        "share_eligible": _pct(uni["share_eligible"], 0),
        "avg_score": _n(uni["avg_score"]),
        "median_score": _n(uni["median_score"]),
        "avg_coverage": _pct(uni["avg_coverage"], 0),
        "n_golden": _int(uni["n_golden"]),
        "n_death": _int(uni["n_death"]),
        "breadth": _pct(uni["breadth_above_sma200"], 0),
        "zones": zones,
        "classes": klass,
        "n_candidates_total": _int(uni["n_candidates_total"]),
        "top_candidates": _table(uni["top_candidates"], stock_spec),
        "sell_zone": _table(uni["sell_zone"], stock_spec, limit=PDF_MAX_ROWS),
        "sector_candidates": _table(uni["sector_candidates"], sector_spec),
    }


def _matrix_ctx(block: dict) -> dict | None:
    if not block.get("available"):
        return None
    m = block["zone_matrix"]
    rows = []
    for i, idx in enumerate(m.index):
        cells = []
        for j, v in enumerate(m.loc[idx].tolist()):
            v = int(v)
            cls = "is-zero" if not v else ("is-diag" if i == j else "is-off")
            cells.append({"v": v, "cls": cls})
        rows.append({"label": str(idx), "cells": cells})
    return {
        "columns": [str(c) for c in m.columns],
        "rows": rows,
        "date": _date(block["snapshot_date"]),
    }


def _changes_block_ctx(block: dict, cols: dict, label: str) -> dict:
    if not block.get("available"):
        return {"available": False, "note": block.get("note", ""), "label": label}
    delta_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col("score_prev", "vorher", _n, "r"), _col("score_now", "heute", _n, "r"),
        _col("delta_score", "Δ", _signed, "r", _sign_class),
        _col("zone_prev", "Zone vorher", _s, "l", _zone_cls), _col("zone_now", "Zone heute", _s, "l", _zone_cls),
    ]
    filter_spec = delta_spec[:3] + [_col("score_now", "Score", _n, "r"), _col("zone_prev", "Zone vorher", _s, "l", _zone_cls), _col("filter_reasons", "Filtergründe", _list)]
    # Kompakte Variante für halbbreite Spalten (Matrix daneben, Mover nebeneinander).
    compact_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col("score_prev", "vorher", _n, "r"), _col("score_now", "heute", _n, "r"),
        _col("delta_score", "Δ", _signed, "r", _sign_class),
    ]
    return {
        "available": True,
        "label": label,
        "date": _date(block["snapshot_date"]),
        "n_snapshot": _int(block["n_snapshot"]),
        "n_both": _int(block["n_both"]),
        "n_new": _int(block["n_new"]),
        "n_gone": _int(block["n_gone"]),
        "new_tickers": ", ".join(str(r.get("ticker")) for r in block["new_in_universe"][:12]) + (" …" if len(block["new_in_universe"]) > 12 else ""),
        "gone_tickers": ", ".join(str(r.get("ticker")) for r in block["gone_from_universe"][:12]) + (" …" if len(block["gone_from_universe"]) > 12 else ""),
        "spearman": _n(block["spearman_rho"], 2),
        "zone_turnover": _pct(block["zone_turnover"]),
        "n_zone_changed": _int(block["n_zone_changed"]),
        "avg_abs_delta": _n(block["avg_abs_delta"]),
        "zone_counts_prev": {z: _int(v) for z, v in block["zone_counts_prev"].items()},
        "movers_up": _table(block["movers_up"], compact_spec, limit=TOP_LIMIT),
        "movers_down": _table(block["movers_down"], compact_spec, limit=TOP_LIMIT),
        "new_candidates": _table(block["new_candidates"], delta_spec, limit=PDF_MAX_ROWS),
        "new_candidates_compact": _table(block["new_candidates"], compact_spec, limit=PDF_MAX_ROWS),
        "dropped_candidates": _table(block["dropped_candidates"], delta_spec, limit=PDF_MAX_ROWS),
        "new_sell": _table(block["new_sell"], delta_spec, limit=PDF_MAX_ROWS),
        "new_filter_fail": _table(block["new_filter_fail"], filter_spec, limit=PDF_MAX_ROWS),
        "matrix": _matrix_ctx(block),
    }



def _changes_ctx(report: dict, cols: dict, comp: dict) -> dict:
    ch = report["changes"]
    watch_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col(cols["score"], "Score", _n, "r"),
    ]
    if cols["pct"]:
        watch_spec.append(_col(cols["pct"], "Perzentil", lambda v: _pct(v, 1), "r"))
    watch_spec += [_col(cols["zone"], "Zone", _s, "l", _zone_cls), _col("sma_signal", "SMA")]
    sig_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col(cols["score"], "Score", _n, "r"), _col(cols["zone"], "Zone", _s, "l", _zone_cls),
        _col("momentum_prev", "vorher"), _col("momentum", "heute"), _col("state_since", "seit", _date),
    ]
    return {
        "available": True,
        "note": ch.get("note", ""),
        "vs_prev": _changes_block_ctx(ch["vs_prev"], cols, "Vorimport"),
        "vs_month": _changes_block_ctx(ch["vs_month"], cols, "~1 Monat"),
        "same_as_prev": bool(comp.get("same_as_prev")),
        "watchlist_entry": _table(ch.get("watchlist_entry") or [], watch_spec, limit=PDF_MAX_ROWS),
        "candidate_signal_changes": _table(ch.get("candidate_signal_changes") or [], sig_spec, limit=PDF_MAX_ROWS),
    }


def _sectors_ctx(report: dict, comp: dict) -> dict:
    sec = report["sectors"]
    if not sec.get("available"):
        return {"available": False, "note": sec.get("note", "")}
    spec = [
        _col("sector", "Sektor"), _col("n", "n", _int, "r"), _col("score", "Score", _n, "r"),
        _col("delta_prev", "Δ Vorimp.", _signed, "r", _sign_class),
    ]
    if not comp.get("same_as_prev"):
        spec.append(_col("delta_month", "Δ 1M", _signed, "r", _sign_class))
    spec += [
        _col("ret_1m", "1M", lambda v: _signed(v, 1, " %"), "r", _sign_class),
        _col("ret_3m", "3M", lambda v: _signed(v, 1, " %"), "r", _sign_class),
        _col("ret_12m", "12M", lambda v: _signed(v, 1, " %"), "r", _sign_class),
        _col("mom_12_1", "12M−1M", lambda v: _signed(v, 1), "r", _sign_class),
        _col("sma200_dist", "SMA-200", lambda v: _signed(v, 1, " %"), "r", _sign_class),
        _col("breadth_sma200", "Breadth", lambda v: _n(v, 0) + " %" if not _is_missing(v) else "–", "r"),
        _col("rrg", "Quadrant", _s, "l", _rrg_cls),
        _col("share_candidates", "Kand.", lambda v: _pct(v, 0), "r"),
        _col("active_weight", "PF aktiv", _pp, "r", _sign_class),
    ]
    quadrants = [
        {"name": q, "sub": sub, "cls": _RRG_CLASS[q], "sectors": sec["quadrants"].get(q, [])}
        for q, sub in (
            (RRG_IMPROVING, "schwach, aber dreht"), (RRG_LEADING, "stark im Trend"),
            (RRG_LAGGING, "schwach & schwach"), (RRG_WEAKENING, "noch oben, schwächt"),
        )
    ]
    changed = [f"{r['sector']}: {r['rrg_prev']} → {r['rrg']}" for r in sec["rows"] if r.get("rrg_changed")]
    low_conf = [r["sector"] for r in sec["rows"] if r.get("low_confidence")]
    return {
        "available": True,
        "table": _table(sec["rows"], spec),
        "quadrants": quadrants,
        "changed": changed,
        "low_confidence": ", ".join(low_conf),
    }


def _exposure_table(rows: list[dict], band: float) -> dict:
    spec = [
        _col("name", "Gruppe"), _col("portfolio", "Portfolio", _pct, "r"), _col("benchmark", "Benchmark", _pct, "r"),
        _col("active", "aktiv", _pp, "r", _sign_class), _col("band_breach", f"> ± {fmt_de(band * 100, 0)} %", lambda v: "⚠" if v else "", "c", _bool_cls),
    ]
    return _table(rows, spec)


def _factor_table(rows: list[dict]) -> dict:
    spec = [
        _col("factor", "Faktor", lambda v: str(v).capitalize()), _col("pf_weighted", "Portfolio (gew.)", lambda v: _n(v, 2), "r", _sign_class),
        _col("pf_mean", "Portfolio (Ø)", lambda v: _n(v, 2), "r", _sign_class), _col("candidates_mean", "Kandidaten (Ø)", lambda v: _n(v, 2), "r", _sign_class),
        _col("universe_mean", "Universum (Ø)", lambda v: _n(v, 2), "r", _sign_class),
    ]
    return _table(rows, spec)


def _portfolio_ctx(report: dict, cols: dict, comp: dict) -> dict:
    pf = report["portfolio"]
    if not pf.get("available"):
        return {"available": False, "note": pf.get("note", "")}
    pos_spec = [
        _col("weight", "Gew.", lambda v: _pct(v, 1), "r"), _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col(cols["score"], "Score", _n, "r"), _col(cols["klass"], "Kl."), _col(cols["zone"], "Zone", _s, "l", _zone_cls),
    ]
    if cols["z"]:
        for f in V2_FACTOR_NAMES:
            pos_spec.append(_col(f"z_{f}", f"Z {f[:3].capitalize()}", lambda v: _n(v, 2), "r", _sign_class))
    pos_spec += [
        _col("sma_signal", "SMA"), _col("trend_phase", "Trend"),
        _col("agent_rating", "Agent"),
        _col("delta_prev", "Δ Vorimp.", _signed, "r", _sign_class),
        _col("flags", "Flags", _list),
    ]
    imm_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"), _col("weight", "Gewicht", _pct, "r"),
        _col(cols["score"], "Score", _n, "r"), _col(cols["zone"], "Zone", _s, "l", _zone_cls), _col("reason", "Grund"),
    ]
    sig_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("weight", "Gewicht", _pct, "r"),
        _col("momentum_prev", "Signal vorher"), _col("momentum_now", "Signal heute"), _col("state_since", "seit", _date),
        _col(cols["score"], "Score", _n, "r"), _col(cols["zone"], "Zone", _s, "l", _zone_cls),
    ]
    zone_change_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("weight", "Gewicht", _pct, "r"),
        _col("zone_prev", "Zone vorher", _s, "l", _zone_cls), _col(cols["zone"], "Zone heute", _s, "l", _zone_cls),
        _col("score_prev", "Score vorher", _n, "r"), _col(cols["score"], "Score heute", _n, "r"), _col("delta_prev", "Δ", _signed, "r", _sign_class),
    ]
    watch_spec = [
        _col("ticker", "Ticker"), _col("name", "Name"), _col("weight", "Gewicht", _pct, "r"), _col(cols["score"], "Score", _n, "r"),
    ] + ([_col(cols["pct"], "Perzentil", _pct, "r")] if cols["pct"] else []) + [_col(cols["zone"], "Zone", _s, "l", _zone_cls)]
    conc = pf["concentration"]
    zone_rows = [
        {"zone": z, "n": _int(pf["zone_counts"].get(z, 0)), "weight": _pct(pf["zone_weights"].get(z, 0.0), 0), "cls": _zone_cls(z), "width": round(100 * pf["zone_weights"].get(z, 0.0), 1)}
        for z in cols["zone_values"]
    ]
    kpis = [
        _kpi("Gew. Score", _n(pf["weighted_avg_score"]), f"Universum Ø {_n(pf['universe_avg_score'])}"),
        _kpi("Δ vs. Vorimport", _signed(pf.get("delta_weighted_prev")), _date(comp.get("prev")) if comp.get("prev") else "", _sign_class(pf.get("delta_weighted_prev"))),
    ]
    if not comp.get("same_as_prev"):
        kpis.append(_kpi("Δ vs. ~1M", _signed(pf.get("delta_weighted_month")), _date(comp.get("month")) if comp.get("month") else "", _sign_class(pf.get("delta_weighted_month"))))
    kpis += [
        _kpi("Positionen", f"{_int(pf['n_resolved'])} / {_int(pf['n_positions'])}", "im Universum / gesamt"),
        _kpi("Flags", _int(pf["n_flagged"]), f"{_int(pf['n_filter_fail'])} FILTER · {_int(pf['n_sell_zone'])} VERKAUFEN", "is-down" if pf["n_filter_fail"] or pf["n_sell_zone"] else ""),
        _kpi("Top-10", _pct(conc.get("top10_weight"), 0), f"eff. N {_n(conc.get('effective_n'), 1)}"),
        _kpi("Über Cap", _int(conc.get("n_above_cap")), f"Cap {_pct(conc.get('cap'), 0)}", "is-warn" if conc.get("n_above_cap") else ""),
    ]
    return {
        "available": True,
        "name": pf["name"],
        "has_weights": pf["has_weights"],
        "missing": ", ".join(pf["missing"]) or "",
        "ambiguous": ", ".join(pf["ambiguous"]) or "",
        "kpis": kpis,
        "zones": zone_rows,
        "classes": {k: _int(v) for k, v in pf["class_counts"].items()},
        "positions": _table(pf["positions"], pos_spec, limit=PDF_MAX_POSITIONS),
        "flags_table": _table(pf["flags_table"], pos_spec, limit=PDF_MAX_ROWS, empty="Keine Position mit Handlungs-Flag."),
        "immediate": _table(pf["immediate_actions"], imm_spec, empty="Keine Sofortmaßnahmen — keine Position in FILTER oder VERKAUFEN."),
        "signal_changes": _table(pf["signal_changes"], sig_spec, empty="Keine Signalwechsel seit dem Vorimport."),
        "zone_changes": _table(pf["zone_changes"], zone_change_spec, empty="Keine Zonenwechsel seit dem Vorimport."),
        "watchlist_exit": _table(pf["watchlist_exit"], watch_spec, empty="Keine Position knapp über der Ausstiegsschwelle."),
        "sector_exposure": _exposure_table(pf["sector_exposure"], pf["sector_band"]),
        "region_exposure": _exposure_table(pf["region_exposure"], pf["region_band"]),
        "factor_exposure": _factor_table(pf["factor_exposure"]),
        "concentration": [
            ("Positionen", _int(conc["n_positions"])), ("Top-5-Gewicht", _pct(conc.get("top5_weight"))),
            ("Top-10-Gewicht", _pct(conc.get("top10_weight"))), ("Max. Einzelgewicht", _pct(conc.get("max_weight"))),
            ("HHI", _n(conc.get("hhi"), 3)), ("Effektive Titelzahl", _n(conc.get("effective_n"), 1)),
            (f"Positionen über Cap ({_pct(conc.get('cap'), 0)})", _int(conc.get("n_above_cap"))),
            ("Gewichte aus Upload", "ja" if pf["has_weights"] else "nein (1/N)"),
        ],
    }


def _model_ctx(report: dict, cols: dict) -> dict:
    mp = report["model_portfolio"]
    if not mp.get("available"):
        return {"available": False, "note": mp.get("note", "")}
    trade_spec = [
        _col("action", "Aktion", _s, "l", _action_cls), _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col("weight_current", "aktuell", _pct, "r"), _col("weight_target", "Ziel", _pct, "r"), _col("delta_w", "Δw", _pp, "r", _sign_class),
        _col("zone_v2", "Zone", _s, "l", _zone_cls), _col("reason", "Grund"), _col("trend_warning", "DC", lambda v: "⚠" if v else "", "c", _bool_cls),
    ]
    zone_now_col = f"{cols['zone']}_now"
    mp_spec = [
        _col("weight_effective", "Gew.", _pct, "r"), _col("ticker", "Ticker"), _col("name", "Name"), _col("sector", "Sektor"),
        _col("composite_z_at_run", "Z (Lauf)", lambda v: _n(v, 2), "r"), _col("zone_at_run", "Zone (Lauf)", _s, "l", _zone_cls),
        _col(zone_now_col, "Zone heute", _s, "l", _zone_cls), _col(f"{cols['score']}_now", "Score heute", _n, "r"),
        _col("cte", "cTE", lambda v: _pct(v, 2), "r"), _col("action", "Aktion (Lauf)", _s, "l", _action_cls),
    ]
    ov_spec = [
        _col("id", "#", _int), _col("ticker", "Titel"), _col("direction", "Richtung"), _col("target_weight", "Zielgewicht", _pct, "r"),
        _col("owner", "Verantwortlich"), _col("expires_at", "Läuft ab", _date), _col("days_left", "Resttage", _int, "r", lambda v: "is-down" if (not _is_missing(v) and float(v) <= OVERRIDE_DUE_DAYS) else ""),
        _col("reason", "Begründung"),
    ]
    vs_spec = [
        _col("name", "Sektor"), _col("model", "Modell", _pct, "r"), _col("current", "Bestand", _pct, "r"), _col("delta", "Δ", _pp, "r", _sign_class),
    ]
    meta_rows = [
        ("Lauf vom", _date(mp["run_date"]) + (f" ({_int(mp['age_days'])} Tage alt)" if mp.get("age_days") is not None else "")),
        ("Rebalance-Modus", _s(mp["rebalance_mode"])), ("Titel", _int(mp["n_titles"])),
        ("TE ex-ante", _pct(mp.get("te_ex_ante"), 2) + (f" (Abdeckung {_pct(mp.get('te_coverage'), 0)})" if mp.get("te_coverage") is not None else "")),
        ("Turnover (einseitig)", _pct(mp["turnover_oneway"]) + (f" · gespeichert {_pct(mp.get('turnover_saved'))}" if mp.get("turnover_saved") is not None else "")),
        ("Trades", f"{_int(mp['n_buy'])} Kauf · {_int(mp['n_sell'])} Verkauf · {_int(mp['n_increase'])} Aufstocken · {_int(mp['n_reduce'])} Reduzieren · {_int(mp['n_deferred'])} verschoben"),
        ("Bestandsportfolio", _s(mp["source_portfolio_name"])), ("Konstruktionsprofil", _s(mp.get("pc_profile_name") or "Standard")),
        ("Settings-Hash", f"{mp['settings_hash'][:16]}… " + ("(unverändert)" if mp["settings_hash_matches"] else "(ABWEICHEND — Lauf erneuern)")),
        ("Zonenwechsel seit Lauf", _int(mp["n_zone_drift"])),
    ]
    return {
        "available": True,
        "meta_rows": meta_rows,
        "settings_hash_matches": mp["settings_hash_matches"],
        "trades": _table(mp["trades"], trade_spec, limit=PDF_MAX_TRADES, empty="Keine Trades — alle Positionen HALTEN."),
        "portfolio": _table(mp["portfolio"], mp_spec, limit=PDF_MAX_POSITIONS),
        "sector_exposure": _exposure_table(mp["sector_exposure"], mp["sector_band"]),
        "region_exposure": _exposure_table(mp["region_exposure"], mp["region_band"]),
        "factor_exposure": _factor_table(mp["factor_exposure"]),
        "vs_active": _table(mp["vs_active"], vs_spec),
        "overrides": _table(mp["overrides"], ov_spec, empty="Keine aktiven Overrides."),
        "n_overrides_due": _int(mp["n_overrides_due"]),
        "run_diagnostics": [
            {"severity": d.severity, "code": d.code, "uid": d.uid or "", "message": d.message, "cls": "is-down" if d.severity == "Fehler" else ("is-warn" if d.severity == "Warnung" else "")}
            for d in mp.get("run_diagnostics", []) if d.severity != "Info"
        ][:PDF_MAX_ROWS],
    }


def _factor_timing_ctx(report: dict) -> dict:
    ft = report["factor_timing"]
    if not ft.get("available"):
        return {"available": False, "note": ft.get("note", "")}
    spec = [
        _col("factor", "Faktor"), _col("strategic", "Strategisch", _pct, "r"), _col("tactical", "Taktisch", _pct, "r"), _col("tilt", "Tilt", _pp, "r", _sign_class),
    ]
    input_labels = {
        "pmi": "ISM-PMI", "pmi_trend": "PMI-Trend", "cli": "OECD CLI", "spread": "10Y−2Y (pp)", "cpi": "CPI (%)",
        "vix": "VIX", "credit": "Credit-OAS (bp)", "pcr": "Put/Call", "mom_value": "Mom. Value", "mom_quality": "Mom. Quality",
        "mom_growth": "Mom. Growth", "mom_momentum": "Mom. Momentum", "mom_lowvol": "Mom. Low Vol",
    }
    return {
        "available": True,
        "regime": ft["regime"],
        "regime_since": _date(ft["regime_since"]),
        "regime_date": _date(ft["regime_date"]),
        "mode": ft["mode"],
        "mode_note": "taktische Gewichte steuern das Composite" if ft["mode"] == "active" else "nur Anzeige — strategische Gewichte bleiben im Composite",
        "weights": _table(ft["weights"], spec),
        "inputs": [(input_labels.get(k, k), _n(v, 1)) for k, v in (ft.get("inputs") or {}).items()],
        "momentum": [(k, _signed(v, 1, " pp")) for k, v in (ft.get("momentum_proxies") or {}).items()],
        "value_spread": _n(ft.get("value_spread"), 2),
        "timeline": [f"{_date(t['date'])} {t['regime']}" for t in ft.get("timeline", [])],
    }


def _risk_ctx(report: dict) -> dict:
    rk = report["risk"]
    if not rk.get("available"):
        return {"available": False, "note": rk.get("note", "")}
    ex = rk.get("expost") or {}
    kpis = [
        _kpi("TE ex-ante", _pct(rk.get("te_ex_ante")), f"Sample {_pct(rk.get('te_sample'))}" if rk.get("te_sample") is not None else rk.get("mcte_error", "")),
        _kpi("TE ex-post 1J", _pct(ex.get("te_1j")), f"Gesamt {_pct(ex.get('te_gesamt'))}"),
        _kpi("Aktive Rendite p. a.", _spct(ex.get("aktive_rendite_pa")), f"IR {_n(ex.get('information_ratio'), 2)}", _sign_class(ex.get("aktive_rendite_pa"))),
        _kpi("Aktives Beta", _n(ex.get("aktives_beta"), 2), f"Korrelation {_n(ex.get('korrelation'), 2)}"),
        _kpi("Max. rel. Drawdown", _pct(ex.get("max_rel_drawdown")), f"{_int(ex.get('n_tage'))} Handelstage", "is-down"),
    ]
    rank_spec = [
        _col("ticker", "Titel"), _col("sector", "Sektor"), _col("gewicht", "Gewicht", _pct, "r"), _col("mcte", "MCTE", lambda v: _pct(v, 2), "r"),
        _col("cte", "cTE", lambda v: _pct(v, 2), "r"), _col("cte_bp", "bp", lambda v: _n(v, 0), "r"),
        _col("composite_score", "Score", _n, "r"), _col("zone_v2", "Zone", _s, "l", _zone_cls), _col("sma_signal", "SMA"),
    ]
    scen_spec = [
        _col("name", "Szenario"), _col("start", "Start", _date), _col("ende", "Ende", _date), _col("pf_rendite", "Portfolio", _spct, "r", _sign_class),
        _col("bm_rendite", "Benchmark", _spct, "r", _sign_class), _col("aktiv", "aktiv", _spct, "r", _sign_class), _col("max_drawdown", "Max. DD", _pct, "r"),
        _col("coverage", "Abdeckung", lambda v: _pct(v, 0), "r"), _col("belastbar", "belastbar", _bool),
    ]
    shock_spec = [
        _col("szenario", "Schock"), _col("pf_pnl", "Portfolio", _spct, "r", _sign_class), _col("bm_pnl", "Benchmark", _spct, "r", _sign_class),
        _col("aktiv", "aktiv", _spct, "r", _sign_class), _col("abdeckung", "Abdeckung", lambda v: _pct(v, 0), "r"), _col("n_geringe_guete", "geringe Güte", _int, "r"),
    ]
    sector_spec = [_col("sektor", "Sektor"), _col("gewicht", "Gewicht", _pct, "r"), _col("cte", "cTE", lambda v: _pct(v, 2), "r"), _col("cte_bp", "bp", lambda v: _n(v, 0), "r")]
    return {
        "available": True,
        "benchmark": _s(rk.get("benchmark")),
        "kpis": kpis,
        "ranking": _table(rk["ranking"], rank_spec),
        "sector_cte": _table(rk["sector_cte"], sector_spec),
        "scenarios": _table(rk["scenarios"], scen_spec),
        "shocks": _table(rk["shocks"], shock_spec, empty=rk.get("shock_error") or "Keine Schock-Ergebnisse."),
        "unresolved": ", ".join(rk["unresolved"]),
        "missing_cache": ", ".join(rk["missing_cache"]),
    }


def _agents_ctx(report: dict, cols: dict) -> dict:
    ag = report["agents"]
    if not ag.get("available"):
        return {"available": False, "note": ag.get("note", "")}
    spec = [
        _col("ticker", "Ticker"), _col("name", "Name"),
        _col("rating", "Rating", _s, "l", lambda v: {"up": "is-up", "down": "is-down", "warn": "is-warn"}.get(AGENT_RATING_TONE.get(str(v), ""), "")),
        _col("created_at", "vom", _date), _col("age_days", "Tage", _int, "r"), _col(cols["score"], "Score", _n, "r"),
        _col(cols["zone"], "Zone", _s, "l", _zone_cls), _col("in_portfolio", "PF", lambda v: "●" if v else "", "c"), _col("conflict", "Konflikt", lambda v: "⚠" if v else "", "c", _bool_cls),
    ]
    return {
        "available": True,
        "note": ag.get("note", ""),
        "counts_line": " · ".join(f"{k} {_int(v)}" for k, v in ag["rating_counts"].items()) or "keine Ratings",
        "n_stale": _int(ag["n_stale"]),
        "n_conflicts": _int(ag["n_conflicts"]),
        "portfolio": _table(ag["portfolio_ratings"], spec, limit=PDF_MAX_ROWS, empty="Keine Agenten-Analysen zu Portfolio-Positionen."),
        "conflicts": _table(ag["conflicts"], spec, limit=PDF_MAX_ROWS, empty="Keine Konflikte zwischen Agenten-Rating und Quant-Zone."),
        "recent": _table(ag["rows"], spec, limit=PDF_MAX_ROWS),
    }


def _data_quality_ctx(report: dict) -> dict:
    dq = report["data_quality"]
    if not dq.get("available"):
        return {"available": False, "note": dq.get("note", "")}
    c = dq["diag_counts"]
    rows = [
        ("Datenstand", f"{_date(dq['asof'])} ({_int(dq['import_age_days'])} Tage alt)" + (" — VERALTET" if dq["stale"] else "")),
        ("Universum", f"{_int(dq['n_universe'])} Titel · Vorimport {_int(dq.get('n_prev'))} · ~1M {_int(dq.get('n_month'))}"),
        ("Archiv", f"{_int(dq['n_snapshots'])} Snapshots seit {_date(dq.get('oldest_snapshot'))}"),
        ("Datenabdeckung", f"Ø {_pct(dq.get('avg_coverage'), 0)} · Median {_pct(dq.get('median_coverage'), 0)} · unter Minimum {_pct(dq.get('share_below_min_coverage'), 1)}"),
        ("Abdeckung je Faktor", " · ".join(f"{k.capitalize()} {_pct(v, 0)}" for k, v in (dq.get("factor_coverage") or {}).items()) or "–"),
        ("Fehlende Pflichtspalten", ", ".join(dq["missing_required_columns"]) or "keine"),
        ("Optionale Spalten", " · ".join(f"{k} {'✓' if v else '–'}" for k, v in dq["optional_columns"].items())),
        ("Titel ohne Sektor / Region / Market Cap", " / ".join(_int(v) for v in dq["missing_fields"].values())),
        ("Ticker-Duplikate", _int(dq["duplicate_tickers"])),
        ("Neutralisierungsschema", ("Drift zu " + ", ".join(dq["neut_scheme_drift"])) if dq["neut_scheme_drift"] else "unverändert gegenüber Vergleichs-Snapshots"),
        ("Signal-Historie", f"{_int(dq['n_signal_snapshots'])} Snapshots"),
        ("Risiko-Kurscache", (f"vorhanden (Stand {_date(dq['risk_cache'].get('last_refreshed'))})" if dq["risk_cache"]["available"] else "fehlt")),
        ("Portfolio-Ticker nicht im Universum", ", ".join(dq["portfolio_missing"]) or "keine"),
        ("Portfolio-Ticker mehrdeutig", ", ".join(dq["portfolio_ambiguous"]) or "keine"),
    ]
    diags = [
        {"severity": d.severity, "code": d.code, "uid": d.uid or "", "message": d.message, "cls": "is-down" if d.severity == "Fehler" else "is-warn"}
        for d in dq["diagnostics"] if d.severity in ("Fehler", "Warnung")
    ]
    report_diags = [
        {"severity": d.severity, "code": d.code, "uid": d.uid or "", "message": d.message, "cls": "is-down" if d.severity == "Fehler" else ("is-warn" if d.severity == "Warnung" else "")}
        for d in report.get("diagnostics", [])
    ]
    return {
        "available": True,
        "rows": rows,
        "counts": f"{_int(c.get('Fehler', 0))} Fehler · {_int(c.get('Warnung', 0))} Warnungen · {_int(c.get('Info', 0))} Infos",
        "diagnostics": diags[:PDF_MAX_ROWS],
        "n_diagnostics": len(diags),
        "report_diagnostics": report_diags[:PDF_MAX_ROWS],
    }


def _param_value(row: dict) -> str:
    v = row.get("value")
    if _is_missing(v):
        return "–"
    if isinstance(v, bool):
        return "ja" if v else "nein"
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x) for x in v)
    if isinstance(v, dict):
        return ", ".join(f"{k}: {x}" for k, x in v.items())
    if row.get("percent") and isinstance(v, (int, float)):
        return _pct(v, 1 if float(v) * 100 % 1 else 0)
    if isinstance(v, float):
        return _n(v, 2)
    return str(v)


def _parameters_ctx(report: dict) -> dict:
    pr = report["parameters"]
    if not pr.get("available"):
        return {"available": False, "note": pr.get("note", "")}
    groups = [
        {"title": g["title"], "rows": [(r["label"], _param_value(r)) for r in g["rows"]]}
        for g in pr["groups"]
    ]
    diffs = [
        (r["label"], _param_value({"value": r["global"], "percent": r["percent"]}), _param_value({"value": r["profile"], "percent": r["percent"]}))
        for r in pr.get("profile_differences", [])
    ]
    return {
        "available": True,
        "settings_hash": pr["settings_hash"],
        "neut_scheme_hash": pr["neut_scheme_hash"],
        "pc_profile_name": pr.get("pc_profile_name") or "Standard (globale Einstellungen)",
        "groups": groups,
        "profile_differences": diffs,
    }


def build_pdf_context(report: dict) -> dict:
    """Formatierter Template-Kontext (alle Werte Strings, Tabellen gekürzt)."""
    meta = report["meta"]
    cols = meta["cols"]
    comp = meta["comparison"]
    asof = meta["asof"]
    comparison_line = (
        f"Vorimport {_date(comp['prev'])}" if comp.get("prev") else "kein Vorimport"
    )
    if comp.get("month") and not comp.get("same_as_prev"):
        comparison_line += f" · ~1 Monat {_date(comp['month'])}"
    elif comp.get("same_as_prev"):
        comparison_line += " (= ~1 Monat)"
    uni = report["universe"]
    pf = report["portfolio"]
    mp = report["model_portfolio"]
    ft = report["factor_timing"]
    dq = report["data_quality"]
    vs_prev = report["changes"].get("vs_prev") or {}
    good = next(iter(sorted(cols["good_zones"])))
    bad = next(iter(sorted(cols["bad_zones"])))

    def _delta_zone(zone: str) -> str:
        if not vs_prev.get("available"):
            return ""
        now = uni["zone_counts"].get(zone, 0) if uni.get("available") else 0
        prev = vs_prev["zone_counts_prev"].get(zone, 0)
        return f"{_signed(now - prev, 0)} vs. Vorimport"

    kpis = []
    if uni.get("available"):
        kpis.append(_kpi("Universum", _int(uni["n"]), f"{_int(uni['n_eligible'])} eligible"))
        kpis.append(_kpi("Kandidaten", _int(uni["zone_counts"].get(good, 0)), _delta_zone(good), "is-up"))
        kpis.append(_kpi(bad.capitalize(), _int(uni["zone_counts"].get(bad, 0)), _delta_zone(bad), "is-down"))
    if pf.get("available"):
        kpis.append(_kpi("PF-Score (gew.)", _n(pf["weighted_avg_score"]), f"{_signed(pf.get('delta_weighted_prev'))} vs. Vorimport" if pf.get("delta_weighted_prev") is not None else f"Universum Ø {_n(pf['universe_avg_score'])}", _sign_class(pf.get("delta_weighted_prev"))))
        kpis.append(_kpi("PF-Flags", _int(pf["n_flagged"]), f"{_int(pf['n_filter_fail'])} FILTER · {_int(pf['n_sell_zone'])} VERKAUFEN", "is-down" if (pf["n_filter_fail"] or pf["n_sell_zone"]) else ""))
    if mp.get("available"):
        kpis.append(_kpi("MP-Trades", _int(mp["n_active_trades"]), f"{_int(mp['n_buy'])} Kauf · {_int(mp['n_sell'])} Verkauf · Turnover {_pct(mp['turnover_oneway'], 0)}"))
    if ft.get("available"):
        kpis.append(_kpi("Regime", ft["regime"], f"seit {_date(ft['regime_since'])}"))
    if dq.get("available"):
        kpis.append(_kpi("Datenstand", _date(dq["asof"]), f"{_int(dq['import_age_days'])} Tage alt", "is-warn" if dq["stale"] else ""))

    sections_flags = {
        "universe": uni.get("available", False),
        "changes": bool(report["changes"].get("vs_prev", {}).get("available") or report["changes"].get("vs_month", {}).get("available")),
        "sectors": report["sectors"].get("available", False),
        "portfolio": pf.get("available", False),
        "model_portfolio": mp.get("available", False),
        "factor_timing": ft.get("available", False),
        "risk": report["risk"].get("available", False),
        "agents": report["agents"].get("available", False),
        "data_quality": dq.get("available", False),
        "parameters": report["parameters"].get("available", False),
    }
    immediate = _portfolio_ctx(report, cols, comp).get("immediate") if pf.get("available") else None
    return {
        "pdf_title": f"{REPORT_TITLE} · {meta['portfolio_name']} · {asof.isoformat()}",
        "author": PDF_AUTHOR,
        "title": REPORT_TITLE,
        "kolhead_left": f"{REPORT_TITLE} · {meta['portfolio_name']} · Stand {_date(asof)}",
        "disclaimer": DISCLAIMER,
        "confidentiality": CONFIDENTIALITY,
        "portfolio_name": meta["portfolio_name"],
        "asof": _date(asof),
        "asof_long": _date_long(asof),
        "generated_at": meta["generated_at"].strftime("%d.%m.%Y %H:%M"),
        "prepared_by": meta.get("prepared_by") or "",
        "scoring_version": "Composite v2" if cols["version"] == "v2" else "Scoring v1",
        "score_label": cols["score_label"],
        "comparison_line": comparison_line,
        "n_universe": _int(meta["n_universe"]),
        "settings_hash": meta["settings_hash"][:16] + "…",
        "key_findings": [
            {"text": f["text"], "tone": f["tone"], "icon": _TONE_ICON.get(f["tone"], "●"), "cls": {"up": "is-up", "down": "is-down", "warn": "is-warn"}.get(f["tone"], "")}
            for f in report.get("key_findings", [])
        ],
        "kpis": kpis,
        "immediate": immediate,
        "sections": sections_flags,
        "universe": _universe_ctx(report, cols, comp),
        "changes": _changes_ctx(report, cols, comp),
        "sectors": _sectors_ctx(report, comp),
        "portfolio": _portfolio_ctx(report, cols, comp),
        "model_portfolio": _model_ctx(report, cols),
        "factor_timing": _factor_timing_ctx(report),
        "risk": _risk_ctx(report),
        "agents": _agents_ctx(report, cols),
        "data_quality": _data_quality_ctx(report),
        "parameters": _parameters_ctx(report),
        "zone_label": cols["zone_label"],
    }


# ── Rendering ──────────────────────────────────────────────────────────────


def render_committee_html(report: dict) -> str:
    """HTML der WeasyPrint-Vorlage (ohne Rendering)."""
    template = _jinja_env.get_template("committee.html.j2")
    return template.render(**build_pdf_context(report))


def render_committee_pdf(report: dict) -> bytes:
    """PDF-Bytes über den WeasyPrint-Worker. Raises :class:`FactsheetRenderError`."""
    html = render_committee_html(report)
    return _run_weasyprint(html, base_url=str(_TEMPLATE_DIR) + "/")


_PRINT_ONLY = re.compile(
    r"@page[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}|@font-face\s*\{[^}]*\}|@import[^;]*;",
    re.S,
)
_RUNNING = re.compile(r"position:\s*running\([^)]*\);?")


def _standalone_css() -> str:
    """CSS der Vorlage inline (agent_report.css + committee.css) ohne
    WeasyPrint-spezifische Regeln (``@page``, ``running()``, ``@font-face``)."""
    parts = []
    for name in ("agent_report.css", "committee.css"):
        path = _TEMPLATE_DIR / name
        if path.exists():
            parts.append(path.read_text(encoding="utf-8"))
    css = "\n".join(parts)
    css = _PRINT_ONLY.sub("", css)
    css = _RUNNING.sub("position: static;", css)
    css += (
        "\nbody { background: #E9E6DE; }"
        "\n.cr-page, .cover { margin: 24px auto; box-shadow: 0 2px 12px rgba(0,0,0,.12); }"
        "\n.kolhead, .pagefoot { display: none; }"
        "\n.pageno::after { content: ''; }"
    )
    return css


def render_committee_fallback_html(report: dict) -> bytes:
    """Eigenständige HTML-Datei (CSS inline) für Umgebungen ohne WeasyPrint."""
    html = render_committee_html(report)
    css = _standalone_css()
    html = re.sub(r'<link rel="stylesheet" href="committee.css"\s*/?>', "", html)
    html = html.replace("</head>", f"<style>\n{css}\n</style>\n</head>", 1)
    return html.encode("utf-8")


def committee_filename(report: dict, ext: str = "pdf") -> str:
    return f"{report['meta']['filename_stem']}.{ext.lstrip('.')}"
