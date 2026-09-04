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
    "bg": "#FBF8F2",
    "surface": "#FFFFFF",
    "border": "#E2DED4",
    "text": "#1A1A19",
    "text_muted": "#6E6E68",
    "accent": "#2A4D38",
    "up": "#2F7A3A",
    "down": "#B23A2E",
    "warn": "#A37F2F",
    "palette": ["#2A4D38", "#6B8F77", "#A37F2F", "#C9A24B", "#3D8A4A", "#BE9136"],
}

DARK = {
    "bg": "#14201A",
    "surface": "#1A2A22",
    "border": "#2A3A30",
    "text": "#F1ECE0",
    "text_muted": "#9DA89E",
    "accent": "#B8D4BE",
    "up": "#6FBF80",
    "down": "#E07B70",
    "warn": "#D8B872",
    "palette": ["#B8D4BE", "#D8B872", "#6FBF80", "#E5C58A", "#9DC3A4", "#C9A24B"],
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


ZONE_TONES = {
    "KANDIDAT": "up",
    "HALTEN": "warn",
    "VERKAUFEN": "down",
    "FILTER": None,
}


def zone_badge(zone: str | None) -> html.Span:
    """Zonen-Chip (Composite v2): KANDIDAT/HALTEN/VERKAUFEN/FILTER."""
    label = str(zone or "–")
    cls = "ms-zone-chip ms-zone-" + label.lower() if zone in ZONE_TONES else "ms-zone-chip"
    return html.Span(label, className=cls)


def diagnostics_panel(
    diags: list,
    title: str = "Diagnose (Composite v2)",
    start_collapsed: bool = True,
) -> html.Div:
    """Diagnoseliste (keine stillen Fallbacks): Zähler-Badges + Liste.

    ``diags`` sind :class:`app.core.diagnostics.Diagnostic`-Objekte. Zeilen
    mit ``uid`` verlinken auf die Einzelanalyse.
    """
    import dash_bootstrap_components as dbc

    from app.core.diagnostics import (
        SEV_ERROR,
        SEV_INFO,
        SEV_WARNING,
        count_by_severity,
        sort_diagnostics,
    )

    if not diags:
        return html.Div()
    diags = sort_diagnostics(diags)
    counts = count_by_severity(diags)
    tone = {SEV_ERROR: "down", SEV_WARNING: "warn", SEV_INFO: "info"}
    badges = [
        ms_badge(sev.upper(), str(counts[sev]), tone=tone[sev])
        for sev in (SEV_ERROR, SEV_WARNING, SEV_INFO)
        if counts.get(sev)
    ]
    rows = []
    for d in diags:
        content: list = [
            html.Span(d.severity, className="ms-diag-sev"),
        ]
        if getattr(d, "uid", None):
            content.append(
                html.A(str(d.uid), href=f"/einzelanalyse?ticker={d.uid}")
            )
        content.append(html.Span(d.message))
        rows.append(
            html.Li(content, className=f"ms-diag-row ms-diag-{d.severity.lower()}")
        )
    body = html.Ul(rows, className="ms-diag-list")
    return html.Div(
        [
            html.Div(badges, className="ms-badge-row"),
            dbc.Accordion(
                [dbc.AccordionItem(body, title=f"{title} ({len(diags)})")],
                start_collapsed=start_collapsed,
                className="mb-3",
            ),
        ]
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
