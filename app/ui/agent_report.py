"""Gemeinsame Render-Bausteine für Agenten-Tiefenanalysen (TradingAgents).

Wird von der Einzelanalyse-Seite und der Ad-hoc-Seite ``/agenten-analyse``
verwendet: Rating-Badge, Fortschritts-Checkliste eines laufenden Jobs und
das gespeicherte Analyseergebnis (Executive Summary + Report-Akkordeon).
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import dcc, html

# Reihenfolge + deutsche Titel der Report-Sektionen aus dem run.json/final-Event.
REPORT_SECTIONS: tuple[tuple[str, str], ...] = (
    ("market_report", "Marktanalyse"),
    ("sentiment_report", "Sentiment-Analyse"),
    ("news_report", "News-Analyse"),
    ("fundamentals_report", "Fundamentalanalyse"),
    ("bull_history", "Bull-These"),
    ("bear_history", "Bear-These"),
    ("investment_plan", "Research-Fazit"),
    ("trader_investment_plan", "Trading-Plan"),
    ("risk_analysis", "Risiko-Debatte"),
    ("final_trade_decision", "Portfolio-Entscheidung"),
)

_RATING_TONE = {
    "Buy": "up",
    "Overweight": "up",
    "Hold": "warn",
    "Underweight": "down",
    "Sell": "down",
}


def rating_badge(rating: str | None) -> html.Span:
    """Rating-Badge im Stil der bestehenden Verdict-Badges."""
    value = rating or "–"
    tone = _RATING_TONE.get(value)
    klass = f"ms-badge is-{tone}" if tone else "ms-badge"
    return html.Span(
        [
            html.Span("Agenten-Rating", className="ms-badge-label"),
            html.Span(value, className="ms-badge-value"),
        ],
        className=klass,
    )


def quant_vs_agents_line(analysis: dict, current_score=None) -> html.Div | None:
    """Vergleichszeile „Quant vs. Agenten“ (Score zum Analysezeitpunkt)."""
    fc = analysis.get("factor_context") or {}
    total = fc.get("total_score", analysis.get("total_score"))
    classification = fc.get("classification", analysis.get("classification"))
    rating = analysis.get("rating")
    if total is None and not rating:
        return None

    parts = []
    if total is not None:
        quant = f"Quant: {total:g}"
        if classification:
            quant += f" ({classification})"
        parts.append(quant)
    if rating:
        parts.append(f"Agenten: {rating}")
    line = "  |  ".join(parts)

    if current_score is not None and not pd.isna(current_score) and total is not None:
        try:
            if abs(float(current_score) - float(total)) >= 0.05:
                line += f"  ·  aktueller Quant-Score: {float(current_score):.1f}"
        except (TypeError, ValueError):
            pass
    return html.Div(line, className="ms-meta")


def progress_checklist(job: dict) -> html.Div:
    """Fortschrittsanzeige eines laufenden Agenten-Jobs.

    ``job`` ist der Registry-Eintrag aus ``agents_client.get_status``:
    ``agents`` (Namen), ``agent_states`` (Name → Status) und ``stage``.
    """
    agents = job.get("agents") or []
    states = job.get("agent_states") or {}

    items = []
    for name in agents:
        status = states.get(name, "pending")
        if status == "completed":
            icon, cls = "✓", "text-success"
        elif status == "in_progress":
            icon, cls = "◐", "text-primary"
        elif status == "error":
            icon, cls = "✗", "text-danger"
        else:
            icon, cls = "○", "ms-tt-muted"
        items.append(
            html.Div(
                [html.Span(icon, className=f"me-2 {cls}"), html.Span(name)],
                className="small",
            )
        )

    return html.Div(
        [
            html.Div(
                [
                    dbc.Spinner(size="sm", color="secondary"),
                    html.Span(
                        job.get("stage") or "Analyse läuft …", className="ms-2"
                    ),
                ],
                className="d-flex align-items-center mb-2",
            ),
            html.Div(items),
            html.Div(
                "Die Tiefenanalyse dauert je nach Modell und Analysetiefe "
                "mehrere Minuten. Die Seite kann verlassen werden — das "
                "Ergebnis wird gespeichert.",
                className="ms-tt-muted small mt-2",
            ),
        ]
    )


def result_view(analysis: dict, current_score=None) -> html.Div:
    """Gespeichertes Analyseergebnis rendern (Badge, Summary, Akkordeon)."""
    reports = analysis.get("reports") or {}
    created = analysis.get("created_at")
    created_label = ""
    if created is not None:
        try:
            created_label = pd.to_datetime(created).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            created_label = str(created)

    header_bits = [rating_badge(analysis.get("rating"))]
    if analysis.get("provider"):
        header_bits.append(
            html.Span(f"Provider: {analysis['provider']}", className="ms-meta ms-2")
        )
    if created_label:
        header_bits.append(
            html.Span(f"Analyse vom {created_label}", className="ms-meta ms-2")
        )

    summary = reports.get("final_trade_decision") or analysis.get("executive_summary")

    accordion_items = [
        dbc.AccordionItem(
            dcc.Markdown(reports[key], className="ms-agent-report"),
            title=title,
            item_id=key,
        )
        for key, title in REPORT_SECTIONS
        if isinstance(reports.get(key), str) and reports[key].strip()
    ]

    children: list = [
        html.Div(header_bits, className="d-flex align-items-center flex-wrap mb-2"),
    ]
    if (line := quant_vs_agents_line(analysis, current_score)) is not None:
        children.append(line)
    if summary:
        children.append(
            html.Div(
                dcc.Markdown(summary, className="ms-agent-report"),
                className="mt-2 mb-3",
            )
        )
    if accordion_items:
        children.append(
            dbc.Accordion(
                accordion_items,
                start_collapsed=True,
                always_open=False,
                flush=True,
            )
        )
    if not summary and not accordion_items:
        children.append(
            html.Div("Kein Reportinhalt gespeichert.", className="ms-tt-muted")
        )
    return html.Div(children)
