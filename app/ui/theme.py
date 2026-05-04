"""Plotly-Template + Layout-Bausteine im Morningstar-Stil.

Farbwerte sind absichtlich 1:1 mit ``app/assets/morningstar.css`` gepflegt,
damit Charts sich nahtlos in die Seite einfügen. Wenn sich dort ein Token
ändert, wird der Wert hier ebenfalls angepasst.
"""

from __future__ import annotations

from typing import Iterable

import plotly.graph_objects as go
import plotly.io as pio
from dash import html


# ---- Farb-Tokens (mirror der CSS-Variablen) ---------------------------------

LIGHT = {
    "bg": "#FFFFFF",
    "surface": "#F7F7F5",
    "border": "#E4E4E0",
    "text": "#1A1A1A",
    "text_muted": "#6B6B68",
    "accent": "#0B3D91",
    "up": "#1B7F3A",
    "down": "#C2281E",
    "warn": "#B8860B",
    "palette": ["#0B3D91", "#3C7CC9", "#1B7F3A", "#C98A0E", "#7A4AA0", "#B8433A"],
}

DARK = {
    "bg": "#0F1114",
    "surface": "#17191D",
    "border": "#2A2D33",
    "text": "#EDEDEA",
    "text_muted": "#9A9A95",
    "accent": "#5B8DEF",
    "up": "#4CAF6E",
    "down": "#E06B60",
    "warn": "#D8B35B",
    "palette": ["#5B8DEF", "#7AA3F2", "#4CAF6E", "#D8B35B", "#A68BD3", "#D8756B"],
}

MS_LIGHT = "ms_light"
MS_DARK = "ms_dark"


def _build_template(tokens: dict) -> go.layout.Template:
    return go.layout.Template(
        layout=go.Layout(
            font=dict(
                family='Inter, "Helvetica Neue", system-ui, sans-serif',
                color=tokens["text"],
                size=12,
            ),
            paper_bgcolor="rgba(0,0,0,0)",
            plot_bgcolor="rgba(0,0,0,0)",
            colorway=tokens["palette"],
            title=dict(
                font=dict(size=14, color=tokens["text"], family="Inter, sans-serif"),
                x=0,
                xanchor="left",
                pad=dict(l=0, t=8, b=8),
            ),
            margin=dict(l=48, r=16, t=32, b=40),
            xaxis=dict(
                gridcolor=tokens["border"],
                linecolor=tokens["border"],
                zerolinecolor=tokens["border"],
                tickfont=dict(color=tokens["text_muted"], size=11),
                title=dict(font=dict(color=tokens["text_muted"], size=11)),
            ),
            yaxis=dict(
                gridcolor=tokens["border"],
                linecolor=tokens["border"],
                zerolinecolor=tokens["border"],
                tickfont=dict(color=tokens["text_muted"], size=11),
                title=dict(font=dict(color=tokens["text_muted"], size=11)),
            ),
            polar=dict(
                bgcolor="rgba(0,0,0,0)",
                radialaxis=dict(
                    gridcolor=tokens["border"],
                    linecolor=tokens["border"],
                    tickfont=dict(color=tokens["text_muted"], size=10),
                ),
                angularaxis=dict(
                    gridcolor=tokens["border"],
                    linecolor=tokens["border"],
                    tickfont=dict(color=tokens["text"], size=11),
                ),
            ),
            legend=dict(
                font=dict(color=tokens["text_muted"], size=11),
                bgcolor="rgba(0,0,0,0)",
                bordercolor=tokens["border"],
            ),
            hoverlabel=dict(
                font=dict(family="Inter, sans-serif", color=tokens["text"]),
                bgcolor=tokens["surface"],
                bordercolor=tokens["border"],
            ),
        )
    )


def register_plotly_templates() -> None:
    """Beide Plotly-Templates registrieren. Idempotent."""
    pio.templates[MS_LIGHT] = _build_template(LIGHT)
    pio.templates[MS_DARK] = _build_template(DARK)
    pio.templates.default = MS_LIGHT


# ---- Layout-Bausteine --------------------------------------------------------


def section_header(title: str, subtitle: str | None = None) -> html.Div:
    """Einheitlicher Section-Trenner: Titel + optional Subtitle-Text rechts."""
    children: list = [html.Div(title, className="ms-section-title")]
    if subtitle:
        children.append(html.Div(subtitle, className="ms-section-sub"))
    return html.Div(children, className="ms-section")


def kpi_band(cells: Iterable[dict]) -> html.Div:
    """Horizontales KPI-Band (Morningstar-Signatur).

    ``cells`` ist eine Liste von Dicts mit keys ``label``, ``value`` und
    optional ``tone`` (``"up"|"down"|"warn"``) sowie ``sub`` (Zusatzzeile).
    """
    cells = list(cells)
    rendered = []
    for c in cells:
        value_cls = "ms-kpi-value"
        tone = c.get("tone")
        if tone == "up":
            value_cls += " is-up"
        elif tone == "down":
            value_cls += " is-down"
        elif tone == "warn":
            value_cls += " is-warn"
        content = [
            html.Div(c["label"], className="ms-kpi-label"),
            html.Div(c["value"], className=value_cls),
        ]
        if c.get("sub"):
            content.append(html.Div(c["sub"], className="ms-kpi-sub"))
        rendered.append(html.Div(content, className="ms-kpi-cell"))
    return html.Div(
        rendered,
        className="ms-kpi-band",
        style={"--ms-kpi-cols": str(len(cells))},
    )


def quote_header(
    ticker: str,
    name: str,
    meta: list[str],
    score_value: str,
    score_label: str,
) -> html.Div:
    """Quote-Header im Morningstar-Stil für Instrument-Detailseiten."""
    meta_children: list = []
    for i, item in enumerate(meta):
        if i > 0:
            meta_children.append(html.Span("·", className="sep"))
        meta_children.append(html.Span(item))
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Span(ticker, className="ms-quote-ticker"),
                            html.Span(name, className="ms-quote-name"),
                        ],
                        className="ms-quote-title",
                    ),
                    html.Div(meta_children, className="ms-quote-meta"),
                ]
            ),
            html.Div(
                [
                    html.Div(score_value, className="ms-quote-score-value"),
                    html.Div(score_label, className="ms-quote-score-label"),
                ],
                className="ms-quote-score",
            ),
        ],
        className="ms-quote",
    )


def ms_badge(
    label: str,
    value: str,
    tone: str | None = None,
) -> html.Span:
    """Kantige Pill mit Label + Wert (z. B. ``PIOTROSKI · 8 / 9``)."""
    cls = "ms-badge"
    if tone in {"up", "down", "warn", "info"}:
        cls += f" is-{tone}"
    return html.Span(
        [
            html.Span(label, className="ms-badge-label"),
            html.Span(value),
        ],
        className=cls,
    )


def factor_breakdown(factors: dict[str, float | None]) -> html.Div:
    """Horizontale Faktor-Leisten (Morningstar-Stil)."""
    from app.ui.formatters import fmt_de

    rows = []
    for name, raw in factors.items():
        if raw is None:
            pct = 0
            display = "-"
        else:
            try:
                pct = max(0, min(100, float(raw)))
                display = fmt_de(pct, 1)
            except (TypeError, ValueError):
                pct = 0
                display = "-"
        rows.append(
            html.Div(
                [
                    html.Div(name, className="ms-factor-label"),
                    html.Div(
                        html.Div(
                            className="ms-factor-bar-fill",
                            style={"width": f"{pct}%"},
                        ),
                        className="ms-factor-bar",
                    ),
                    html.Div(display, className="ms-factor-value"),
                ],
                className="ms-factor-row",
            )
        )
    return html.Div(rows, className="ms-factor-list")
