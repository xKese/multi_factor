"""PDF-Export der Agenten-Berichte (Design-Handoff #5a/#5b).

Zwei Dokumenttypen aus einer gespeicherten Tiefenanalyse
(``persistence.load_agent_analysis``):

- **Einzelbericht** (``render_section_pdf``) — eine Sektion als A4-Dokument
  mit Kennzahlen-Seitenspalte auf Seite 1.
- **Sammelreport** (``render_full_pdf``) — Deckblatt, Überblick mit
  Inhaltsverzeichnis und alle Sektionen, je Bericht auf neuer Seite.

Rendering läuft über dieselbe WeasyPrint-Worker-Pipeline wie das
Quant-Factsheet (``factsheet_pdf``); die Templates liegen bewusst in
``app/factsheet_template/`` — Dash würde CSS aus ``app/assets/`` in alle
App-Seiten injizieren.
"""

from __future__ import annotations

import re
from functools import partial
from typing import Any, Callable

import pandas as pd
from markdown_it import MarkdownIt
from markupsafe import Markup

from app.core.factsheet_pdf import (
    FactsheetRenderError,
    _TEMPLATE_DIR,
    _jinja_env,
    _run_weasyprint,
)
from app.ui.agent_report import (
    delta_quant,
    first_sentences,
    fmt_de_number,
    fmt_local_dt,
    modal_sections,
    rating_tone,
)
from app.ui.formatters import fmt_signed_percent

__all__ = [
    "AGENT_COUNT",
    "DISCLAIMER",
    "PDF_AUTHOR",
    "FactsheetRenderError",
    "build_full_context",
    "build_section_context",
    "render_full_pdf",
    "render_section_pdf",
    "sanitize_filename",
]

PDF_AUTHOR = "Meeder & Seifer Family Office GmbH"
# Feste Pipeline-Größe des TradingAgents-Service — identisch zur App-Copy
# ("12 Agenten · Bull/Bear-Debatte · Risiko-Runde").
AGENT_COUNT = 12
DISCLAIMER = (
    "KI-gestützte Analyse · keine Anlageberatung im Sinne des WpHG · "
    "Meeder & Seifer Family Office GmbH, Lindenstraße 5, "
    "60325 Frankfurt am Main"
)
CONFIDENTIALITY = (
    "Vertraulich · ausschließlich für den bezeichneten Empfänger bestimmt. "
    "KI-gestützte Analyse, keine Anlageberatung und keine Anlageempfehlung "
    "im Sinne des WpHG."
)

_DELTA_CHIP = {
    "bestätigt": ("✓", "Prior bestätigt"),
    "vorsichtiger": ("◆", "Vorsichtiger als Prior"),
    "widerspricht": ("✕", "Widerspricht dem Prior"),
}

_MONTHS_DE = (
    "Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
    "August", "September", "Oktober", "November", "Dezember",
)

# Rohes HTML aus LLM-Output wird escaped (html=False); Tabellen wie im
# Lese-Modal (dcc.Markdown rendert GFM-Tabellen ebenfalls).
_md = MarkdownIt("commonmark", {"html": False}).enable("table")


def _md_html(text: str | None) -> Markup:
    return Markup(_md.render(text or ""))


# ── Dateinamen ─────────────────────────────────────────────────────────────

_TRANSLIT = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue",
     "ß": "ss", "&": "und", "·": "_", " ": "_", "/": "_"}
)


def sanitize_filename(ticker: str, doc_name: str, date_iso: str) -> str:
    """``{TICKER}_{Name}_{YYYY-MM-DD}.pdf`` — Umlaute transliteriert."""
    stem = f"{ticker}_{doc_name}_{date_iso}".translate(_TRANSLIT)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    stem = re.sub(r"_+", "_", stem).strip("_")
    return f"{stem}.pdf"


# ── Wert-Auflösung & Kennzahlen-Mapping ────────────────────────────────────

def _resolve(path: str, fc: dict | None, row: dict | None) -> Any:
    """Wert zu ``"fc.a.b"``- bzw. ``"row.col"``-Pfad — ``None`` wenn fehlend."""
    root, _, rest = path.partition(".")
    node: Any = fc if root == "fc" else row
    for part in rest.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    if node is None or (isinstance(node, float) and pd.isna(node)):
        return None
    return node


def _fmt_piotroski(v: Any) -> str:
    return f"{int(round(float(v)))} / 9"


_num1 = partial(fmt_de_number, decimals=1)

# (Label, Pfad, Formatter, tone_by_sign) — nur tatsächlich vorhandene Werte
# werden gerendert; fehlende Zeilen entfallen ersatzlos (Handoff-Regel).
_Metric = tuple[str, str, Callable[[Any], str], bool]

_FACTOR_SCORES: tuple[_Metric, ...] = (
    ("Score Value", "fc.factor_scores.value", _num1, False),
    ("Score Qualität", "fc.factor_scores.quality", _num1, False),
    ("Score Growth", "fc.factor_scores.growth", _num1, False),
    ("Score Momentum", "fc.factor_scores.momentum", _num1, False),
    ("Score Low Vol", "fc.factor_scores.lowvol", _num1, False),
)

SECTION_METRICS: dict[str, tuple[_Metric, ...]] = {
    "market_report": (
        ("Δ SMA-200", "row.sma_200_distance", fmt_signed_percent, True),
        ("Δ SMA-50", "row.sma_50_distance", fmt_signed_percent, True),
        ("Momentum 12-1", "fc.signals.mom_12_1", fmt_signed_percent, True),
        ("Abstand 52W-Hoch", "fc.signals.dist_52w_high",
         fmt_signed_percent, True),
        ("Trend", "fc.signals.trend_phase", str, False),
    ),
    "sentiment_report": (),
    "news_report": (),
    "fundamentals_report": (
        ("Piotroski", "fc.piotroski", _fmt_piotroski, False),
        ("Altman-Z", "fc.altman_z", _num1, False),
        ("Score Qualität", "fc.factor_scores.quality", _num1, False),
        ("Score Value", "fc.factor_scores.value", _num1, False),
    ),
    "bull_history": (
        ("Score Growth", "fc.factor_scores.growth", _num1, False),
        ("Score Momentum", "fc.factor_scores.momentum", _num1, False),
        ("Momentum 12-1", "fc.signals.mom_12_1", fmt_signed_percent, True),
    ),
    "bear_history": (
        ("Score Value", "fc.factor_scores.value", _num1, False),
        ("Score Low Vol", "fc.factor_scores.lowvol", _num1, False),
        ("Abstand 52W-Hoch", "fc.signals.dist_52w_high",
         fmt_signed_percent, True),
    ),
    "investment_plan": _FACTOR_SCORES,
    "trader_investment_plan": (
        ("Δ SMA-50", "row.sma_50_distance", fmt_signed_percent, True),
        ("Δ SMA-200", "row.sma_200_distance", fmt_signed_percent, True),
        ("Momentum 12-1", "fc.signals.mom_12_1", fmt_signed_percent, True),
        ("SMA-Signal", "fc.signals.sma_signal", str, False),
    ),
    "risk_analysis": (
        ("Altman-Z", "fc.altman_z", _num1, False),
        ("Score Low Vol", "fc.factor_scores.lowvol", _num1, False),
        ("Abstand 52W-Hoch", "fc.signals.dist_52w_high",
         fmt_signed_percent, True),
    ),
    "final_trade_decision": _FACTOR_SCORES,
}

_OVERVIEW_METRICS: tuple[_Metric, ...] = (
    ("Piotroski", "fc.piotroski", _fmt_piotroski, False),
    ("Altman-Z", "fc.altman_z", _num1, False),
    ("Δ SMA-200", "row.sma_200_distance", fmt_signed_percent, True),
    ("Δ SMA-50", "row.sma_50_distance", fmt_signed_percent, True),
    ("Momentum 12-1", "fc.signals.mom_12_1", fmt_signed_percent, True),
    ("Abstand 52W-Hoch", "fc.signals.dist_52w_high",
     fmt_signed_percent, True),
)


def _metrics(
    spec: tuple[_Metric, ...], fc: dict | None, row: dict | None
) -> list[dict]:
    out = []
    for label, path, fmt, toned in spec:
        value = _resolve(path, fc, row)
        if value is None:
            continue
        tone = None
        if toned:
            try:
                v = float(value)
                tone = "pos" if v > 0 else ("neg" if v < 0 else None)
            except (TypeError, ValueError):
                tone = None
        out.append({"label": label, "value": fmt(value), "tone": tone})
    return out


# ── Basis-Bausteine ────────────────────────────────────────────────────────

def _scored_row(scored: pd.DataFrame | None, ticker: str) -> dict | None:
    """Zeile aus ``STATE.scored`` — exakter Ticker, sonst ohne Suffix."""
    if scored is None or scored.empty or not ticker:
        return None
    hit = scored[scored["ticker"] == ticker]
    if hit.empty and "." in ticker:
        hit = scored[scored["ticker"] == ticker.split(".")[0]]
    if hit.empty:
        return None
    return hit.iloc[0].to_dict()


def _company_name(analysis: dict, fc: dict | None, row: dict | None) -> str:
    identity = (fc or {}).get("identity") or {}
    name = identity.get("name") or (row or {}).get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return str(analysis.get("ticker") or "")


def _dates(analysis: dict) -> tuple[str, str, str]:
    """(``21.07.2026``, ``2026-07-21``, ``21. Juli 2026``) aus created_at."""
    created = analysis.get("created_at")
    short = fmt_local_dt(created, "%d.%m.%Y")
    iso = fmt_local_dt(created, "%Y-%m-%d")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        # created_at fehlt oder ist unlesbar (fmt_local_dt gibt Rohtext
        # zurück) — auf heute degradieren statt zu crashen.
        today = pd.Timestamp.now()
        short, iso = today.strftime("%d.%m.%Y"), today.strftime("%Y-%m-%d")
    y, m, d = iso.split("-")
    long = f"{int(d)}. {_MONTHS_DE[int(m) - 1]} {y}"
    return short, iso, long


def _quant_block(fc: dict | None) -> dict | None:
    """Score + Klassenzeile fürs PDF — ``None`` ohne Quant-Kontext."""
    score = (fc or {}).get("total_score")
    if score is None:
        return None
    cls = str((fc or {}).get("classification") or "")
    code, _, label = cls.partition(" - ")
    class_line = (
        f"{code.strip()} · {label.strip().upper()}" if label else cls
    ) or None
    return {
        "score": fmt_de_number(score, 1),
        "class_line": class_line,
        "cover_line": fmt_de_number(score, 1)
        + (f" · {code.strip()}" if label else ""),
    }


def _delta_chip(analysis: dict, fc: dict | None) -> dict | None:
    delta = delta_quant(analysis.get("rating"), fc)
    if not delta:
        return None
    label, tone = delta
    icon, text = _DELTA_CHIP.get(label, ("", label))
    return {"icon": icon, "text": text, "tone": tone}


def _takeaway(text: str | None) -> str:
    return first_sentences(text or "", n=1, max_chars=110)


# ── Kontext-Builder ────────────────────────────────────────────────────────

def build_section_context(
    analysis: dict, section_key: str, row: dict | None
) -> dict:
    """Template-Kontext für den Einzelbericht (#5a)."""
    reports = analysis.get("reports") or {}
    sections = modal_sections(reports)
    keys = [k for k, _, _ in sections]
    if section_key not in keys:
        raise ValueError(f"Kein Bericht für Sektion {section_key!r} vorhanden.")
    idx = keys.index(section_key)
    _, title, role = sections[idx]
    fc = analysis.get("factor_context")
    company = _company_name(analysis, fc, row)
    date_short, date_iso, _ = _dates(analysis)

    meta_parts = [role, date_short]
    if analysis.get("provider"):
        meta_parts.append(str(analysis["provider"]))
    rating = analysis.get("rating")
    quant = _quant_block(fc)
    metrics = _metrics(SECTION_METRICS.get(section_key, ()), fc, row)
    sidebar = {
        "rating": {
            "value": rating,
            "tone": rating_tone(rating),
            "note": f"Konsens aus {AGENT_COUNT} Agenten",
        }
        if rating
        else None,
        "quant": quant,
        "delta": _delta_chip(analysis, fc) if quant else None,
        "metrics": metrics,
        "reports": [
            {"title": t, "active": k == section_key} for k, t, _ in sections
        ],
    }
    if not any((sidebar["rating"], sidebar["quant"], metrics)):
        # Inhaltlich leere Seitenspalte entfällt — Text läuft volle Breite.
        sidebar = None

    return {
        "doc_type": "single",
        "band_right": "Agenten-Tiefenanalyse · Einzelbericht",
        "kolhead_left": f"{company} · {title}",
        "company": company,
        "section_title": title,
        "meta_line": " · ".join(p for p in meta_parts if p),
        "lead": first_sentences(reports.get(section_key), n=2, max_chars=300),
        "body_html": _md_html(reports.get(section_key)),
        "sidebar": sidebar,
        "pdf_title": f"{company} · {title}",
        "author": PDF_AUTHOR,
        "disclaimer": DISCLAIMER,
        "filename": sanitize_filename(
            str(analysis.get("ticker") or ""), title, date_iso
        ),
    }


def build_full_context(analysis: dict, row: dict | None) -> dict:
    """Template-Kontext für den Sammelreport (#5b)."""
    reports = analysis.get("reports") or {}
    section_list = modal_sections(reports)
    if not section_list:
        raise ValueError("Die Analyse enthält keine Berichte.")
    fc = analysis.get("factor_context")
    company = _company_name(analysis, fc, row)
    _, date_iso, date_long = _dates(analysis)
    rating = analysis.get("rating")
    quant = _quant_block(fc)

    sections = []
    for i, (key, title, role) in enumerate(section_list, start=1):
        sections.append(
            {
                "num": f"{i:02d}",
                "key": key,
                "title": title,
                "role": role,
                "takeaway": _takeaway(reports.get(key)),
                "body_html": _md_html(reports.get(key)),
                "metrics": _metrics(SECTION_METRICS.get(key, ()), fc, row),
            }
        )
    report_index = [
        {"num": s["num"], "title": s["title"]} for s in sections
    ]

    summary_src = reports.get("final_trade_decision") or analysis.get(
        "executive_summary"
    )
    return {
        "doc_type": "full",
        "band_right": "Agenten-Tiefenanalyse · Gesamtbericht",
        "kolhead_left": f"{company} · Gesamtbericht",
        "company": company,
        "ticker": str(analysis.get("ticker") or ""),
        "rating": {"value": rating, "tone": rating_tone(rating)}
        if rating
        else None,
        "quant": quant,
        "delta": _delta_chip(analysis, fc) if quant else None,
        "scope_line": f"{len(sections)} Berichte · {AGENT_COUNT} Agenten",
        "date_long": date_long,
        "summary": first_sentences(summary_src, n=3, max_chars=420),
        "sections": sections,
        "report_index": report_index,
        "overview_metrics": _metrics(_OVERVIEW_METRICS, fc, row),
        "pdf_title": f"{company} · Agenten-Tiefenanalyse",
        "author": PDF_AUTHOR,
        "disclaimer": DISCLAIMER,
        "confidentiality": CONFIDENTIALITY,
        "filename": sanitize_filename(
            str(analysis.get("ticker") or ""),
            "Agenten-Tiefenanalyse",
            date_iso,
        ),
    }


# ── Public API ─────────────────────────────────────────────────────────────

def render_section_pdf(
    analysis: dict, section_key: str, scored: pd.DataFrame | None
) -> tuple[bytes, str]:
    """Einzelbericht (#5a) — ``(pdf_bytes, dateiname)``."""
    row = _scored_row(scored, str(analysis.get("ticker") or ""))
    ctx = build_section_context(analysis, section_key, row)
    html = _jinja_env.get_template("agent_single.html.j2").render(**ctx)
    return _run_weasyprint(html, base_url=str(_TEMPLATE_DIR) + "/"), ctx[
        "filename"
    ]


def render_full_pdf(
    analysis: dict, scored: pd.DataFrame | None
) -> tuple[bytes, str]:
    """Sammelreport „Alle Berichte" (#5b) — ``(pdf_bytes, dateiname)``."""
    row = _scored_row(scored, str(analysis.get("ticker") or ""))
    ctx = build_full_context(analysis, row)
    html = _jinja_env.get_template("agent_full.html.j2").render(**ctx)
    return _run_weasyprint(html, base_url=str(_TEMPLATE_DIR) + "/"), ctx[
        "filename"
    ]
