"""Vollständiger Koyfin-/Score-Steckbrief je Titel (Dash-frei).

Der Koyfin-Export enthält deutlich mehr Kennzahlen als das Composite-v2-
Scoring verwendet (Margen, Wachstumsraten, Multiples, Renditen, Bilanz-
Rohdaten). :func:`koyfin_snapshot` legt **alle** importierten Spalten in
Gruppen vor — mit deutschen Labels, Prozent-Flag und formatiertem Text —,
damit Chat und Analyst-Lauf auch dann ein vollständiges Bild haben, wenn
Alpha Vantage (typisch bei europäischen Titeln) nichts liefert.
:func:`uid_history` liefert den Punkt-in-Zeit-Verlauf eines Titels aus
``koyfin_universe_history``.
"""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd

from .. import persistence
from ..schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS, PERCENT_COLUMNS
from app.ui.formatters import fmt_de, fmt_int, fmt_percent
from app.ui.labels import label_for

GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Identität", ("ticker", "name", "sector", "industry", "industry_group", "industry_group_source",
                   "region", "market_cap", "last_price", "export_date", "ipo_date")),
    ("Bewertung", ("pe", "pb", "ps", "pfcf", "ev_ebitda", "ev_ebit", "peg", "div_yield",
                   "fcf_yield", "fcf_yield_v2", "fcf_yield_source")),
    ("Qualität", ("roe", "roa", "roic", "gross_margin", "op_margin", "gp_ta", "accruals", "ocf_ni",
                  "debt_equity", "net_debt_ebitda", "debt_ebit", "int_coverage", "current_ratio")),
    ("Wachstum", ("rev_cagr_3y", "eps_cagr_3y", "fcf_cagr_3y", "fwd_eps_growth", "fwd_rev_growth",
                  "rev_growth_1y", "eps_revisions_3m")),
    ("Momentum/Technik", ("ret_1m", "ret_3m", "ret_6m", "ret_12m", "mom_12_1", "mom_12_1_adj",
                          "high_52w", "low_52w", "range_52w", "dist_52w_high", "sma_20", "sma_50",
                          "sma_200", "sma_50_distance", "sma_200_distance", "sma_signal",
                          "trend_phase", "trend_warning")),
    ("Risiko/Liquidität", ("beta", "volatility_1y", "adv_3m", "avg_volume", "altman_z")),
    ("Piotroski-Rohdaten/Bilanz", ("piotroski", "net_income", "net_income_prev", "ocf", "ocf_prev",
                                   "total_assets", "total_assets_prev", "total_debt", "total_debt_prev",
                                   "current_assets", "current_liab", "current_assets_prev",
                                   "current_liab_prev", "shares_out", "shares_out_prev", "revenue",
                                   "cogs", "revenue_prev", "cogs_prev", "asset_growth", "share_issuance")),
)
_GROUPED = {col for _, cols in GROUPS for col in cols}
OTHER_GROUP = "Weitere Koyfin-Spalten"

# Kennzahlen aus Koyfin, die als Betrag (Mio.) geliefert werden → ganzzahlig.
_INTEGER_LIKE = {"market_cap", "net_income", "net_income_prev", "ocf", "ocf_prev", "total_assets",
                 "total_assets_prev", "total_debt", "total_debt_prev", "current_assets", "current_liab",
                 "current_assets_prev", "current_liab_prev", "shares_out", "shares_out_prev", "revenue",
                 "cogs", "revenue_prev", "cogs_prev", "avg_volume"}
_RATIO_PERCENT = {"accruals", "asset_growth", "share_issuance", "gp_ta", "rev_growth_1y", "mom_12_1",
                  "dist_52w_high", "sma_50_distance", "sma_200_distance", "range_52w", "fcf_yield",
                  "fcf_yield_v2"}

HISTORY_DEFAULT_COLUMNS: tuple[str, ...] = (
    "snapshot_date", "composite_score", "composite_pct", "zone_v2", "classification_v2",
    "z_value", "z_quality", "z_momentum", "z_investment", "last_price", "market_cap", "pe",
    "ret_12m", "piotroski", "sma_signal",
)


def _is_na(value) -> bool:
    if value is None:
        return True
    try:
        return bool(pd.isna(value))
    except (TypeError, ValueError):
        return False


def _text(key: str, value) -> str:
    if _is_na(value):
        return "–"
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return "ja" if value else "nein"
    if isinstance(value, str):
        return value
    if key in PERCENT_COLUMNS or key in _RATIO_PERCENT:
        return fmt_percent(value, 1)
    if key in _INTEGER_LIKE:
        return fmt_int(value)
    try:
        return fmt_de(float(value), 2)
    except (TypeError, ValueError):
        return str(value)


def _val(value):
    if _is_na(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:  # noqa: BLE001
            return value
    return value


def _num(row, key):
    v = row.get(key) if hasattr(row, "get") else None
    if _is_na(v):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def koyfin_snapshot(row, settings=None) -> dict:
    """Alle Koyfin-Spalten (Basis + optional + abgeleitete) in Gruppen plus
    Scores. ``row`` ist eine Zeile aus ``STATE.scored`` (Series oder Dict)."""
    if isinstance(row, pd.Series):
        row = row.to_dict()
    row = dict(row or {})
    present = set(row.keys())
    groups: list[dict] = []
    for title, cols in GROUPS:
        items = []
        for col in cols:
            if col not in present:
                continue
            val = row.get(col)
            items.append({
                "key": col,
                "label": label_for(col),
                "value": _val(val),
                "percent": col in PERCENT_COLUMNS or col in _RATIO_PERCENT,
                "text": _text(col, val),
            })
        if items:
            groups.append({"title": title, "items": items})
    rest = [c for c in list(KOYFIN_COLUMNS) + list(OPTIONAL_COLUMNS) if c in present and c not in _GROUPED]
    if rest:
        groups.append({
            "title": OTHER_GROUP,
            "items": [{"key": c, "label": label_for(c), "value": _val(row.get(c)),
                       "percent": c in PERCENT_COLUMNS, "text": _text(c, row.get(c))} for c in rest],
        })

    version = getattr(settings, "scoring_version", "v2") if settings is not None else "v2"
    v1 = {k: _val(row.get(k)) for k in ("total_score", "classification", "recommendation", "value_score",
                                        "quality_score", "growth_score", "momentum_score", "lowvol_score")
          if k in present}
    v2: dict = {k: _val(row.get(k)) for k in ("composite_score", "composite_pct", "composite_z",
                                               "classification_v2", "zone_v2", "data_coverage_v2") if k in present}
    factors = {}
    for f in ("value", "quality", "momentum", "investment"):
        if f"z_{f}" in present:
            factors[f] = {"z": _num(row, f"z_{f}"), "cov": _num(row, f"cov_{f}")}
    if factors:
        v2["factors"] = factors
    try:
        from ..agents_client import build_composite_v2_context

        if settings is not None:
            block = build_composite_v2_context(row, settings)
            if block.get("indicators"):
                v2["indicators"] = block["indicators"]
            if block.get("segment"):
                v2["segment"] = block["segment"]
    except Exception:  # noqa: BLE001 — Zusatzinfo, nie blockierend
        pass
    reasons = row.get("filter_reasons")
    if isinstance(reasons, str):
        reasons = [reasons] if reasons else []
    elif not isinstance(reasons, (list, tuple)):
        reasons = [] if _is_na(reasons) else list(reasons)
    scores = {
        "version": version,
        "v1": v1,
        "v2": v2,
        "filter": {"pass": _val(row.get("filter_pass")), "reasons": [str(r) for r in reasons]},
        "piotroski": _num(row, "piotroski"),
        "altman_z": _num(row, "altman_z"),
        "sma_signal": _val(row.get("sma_signal")),
        "trend_phase": _val(row.get("trend_phase")),
        "trend_warning": _val(row.get("trend_warning")),
    }
    return {
        "uid": str(row.get("uid") or row.get("ticker") or ""),
        "ticker": str(row.get("ticker") or ""),
        "name": str(row.get("name") or ""),
        "groups": groups,
        "scores": scores,
    }


def render_snapshot_text(snapshot: dict, max_chars: int = 6000) -> str:
    """Kompakter Markdown-Steckbrief (Scores zuerst, dann Gruppen)."""
    sc = snapshot.get("scores") or {}
    v2 = sc.get("v2") or {}
    v1 = sc.get("v1") or {}
    lines = [f"**{snapshot.get('ticker')} — {snapshot.get('name')}** (uid {snapshot.get('uid')})", "", "### Scores"]
    if v2:
        lines.append(
            f"- Composite v2: {fmt_de(v2.get('composite_score'), 1)} / 100 · Perzentil "
            f"{fmt_percent(v2.get('composite_pct'), 1)} · Klasse {v2.get('classification_v2') or '–'} · "
            f"Zone {v2.get('zone_v2') or '–'}"
        )
        if v2.get("factors"):
            parts = [f"{f} {fmt_de(d.get('z'), 2)}" for f, d in v2["factors"].items()]
            lines.append("- Faktor-Z: " + ", ".join(parts))
    if v1:
        lines.append(
            f"- Score v1: {fmt_de(v1.get('total_score'), 1)} · {v1.get('classification') or '–'} · "
            f"{v1.get('recommendation') or '–'}"
        )
    filt = sc.get("filter") or {}
    passed = filt.get("pass")
    lines.append(
        f"- Universumsfilter: {'bestanden' if passed else ('nicht bestanden' if passed is not None else '–')}"
        + (f" ({', '.join(filt.get('reasons') or [])})" if filt.get("reasons") else "")
    )
    lines.append(
        f"- Piotroski {fmt_de(sc.get('piotroski'), 0)} · Altman Z {fmt_de(sc.get('altman_z'), 2)} · "
        f"SMA {sc.get('sma_signal') or '–'} · Trend {sc.get('trend_phase') or '–'}"
    )
    for g in snapshot.get("groups") or []:
        lines.append("")
        lines.append(f"### {g['title']}")
        for it in g["items"]:
            lines.append(f"- {it['label']}: {it['text']}")
    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[: max_chars - 40].rstrip() + f"\n… [gekürzt auf {max_chars} Zeichen]"
    return text


def uid_history(uid: str, columns: tuple[str, ...] | list[str] | None = None, n: int = 8) -> list[dict]:
    """Verlauf eines Titels über die letzten ``n`` Snapshots (aufsteigend);
    nur vorhandene Spalten, NaN → None, Datum ISO."""
    df = persistence.load_uid_history(uid, limit=max(1, int(n)))
    if df is None or df.empty:
        return []
    cols = [c for c in (columns or HISTORY_DEFAULT_COLUMNS) if c in df.columns]
    if "snapshot_date" in df.columns and "snapshot_date" not in cols:
        cols = ["snapshot_date"] + cols
    out = []
    for _, r in df.iterrows():
        out.append({c: _val(r.get(c)) for c in cols})
    return out


def render_history_text(rows: list[dict]) -> str:
    """Markdown-Tabelle des Verlaufs (Dezimalkomma)."""
    if not rows:
        return "(kein Verlauf gespeichert)"
    cols = list(rows[0].keys())
    head = "| " + " | ".join(label_for(c) if c != "snapshot_date" else "Stand" for c in cols) + " |"
    sep = "| " + " | ".join("---" for _ in cols) + " |"
    body = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if c == "snapshot_date":
                cells.append(str(v or "–"))
            elif isinstance(v, (int, float)) and not isinstance(v, bool):
                cells.append(_text(c, v))
            else:
                cells.append("–" if v is None else str(v))
        body.append("| " + " | ".join(cells) + " |")
    note = "" if len(rows) >= 2 else "\n(nur ein Snapshot — kein Verlauf)"
    return "\n".join([head, sep, *body]) + note
