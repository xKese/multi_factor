"""Dashboard-Seite im Morningstar-Stil."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.express as px
from dash import Input, Output, State, callback, dcc, html, no_update, register_page

from app.core.state import STATE
from app.pages.common import format_scored, page_title, render_table
from app.ui import MS_LIGHT, fmt_de, fmt_percent, kpi_band, section_header


def _empty_state() -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div("Erste Schritte", className="ms-empty-eyebrow"),
                    html.H2(
                        "Noch keine Daten geladen",
                        className="ms-empty-title",
                    ),
                    html.P(
                        "Lade einen Koyfin-CSV-Export hoch, um Dashboard, "
                        "Einzelanalyse und SMA-Signale zu befüllen.",
                        className="ms-empty-sub",
                    ),
                    html.Div(
                        [
                            dcc.Link(
                                "CSV hochladen",
                                href="/daten-import",
                                className="btn btn-primary ms-empty-cta",
                            ),
                            dcc.Link(
                                "Anleitung lesen",
                                href="/anleitung",
                                className="btn btn-outline-secondary ms-empty-cta",
                            ),
                        ],
                        className="ms-empty-actions",
                    ),
                    html.Div(
                        [
                            html.Div(
                                [
                                    html.Div("Erwartetes Format", className="ms-empty-hint-title"),
                                    html.Div(
                                        "Koyfin-Screener-Export · Semikolon-getrennt · 57 Spalten · "
                                        "erste zwei Zeilen Metadaten",
                                        className="ms-empty-hint-body",
                                    ),
                                ]
                            ),
                            html.Div(
                                [
                                    html.Div("Was passiert dann?", className="ms-empty-hint-title"),
                                    html.Div(
                                        "Perzentil-Ränge, Faktor-Scores und Empfehlungen werden "
                                        "automatisch berechnet. Filter und Gewichte sind änderbar.",
                                        className="ms-empty-hint-body",
                                    ),
                                ]
                            ),
                        ],
                        className="ms-empty-hints",
                    ),
                ],
                className="ms-empty-card",
            )
        ],
        className="ms-empty-wrap",
    )


TOP50_COLUMNS = [
    "ticker",
    "name",
    "sector",
    "industry",
    "region",
    "total_score",
    "classification",
    "recommendation",
]


def _build_sector_fig(df: pd.DataFrame, active_sector: str | None):
    sector_avg = (
        df.dropna(subset=["total_score"])
        .groupby("sector")["total_score"]
        .mean()
        .sort_values(ascending=True)
    )
    sectors = sector_avg.index.tolist()
    values = sector_avg.values.tolist()
    fig = px.bar(
        x=values,
        y=sectors,
        orientation="h",
        labels={"x": "Ø Gesamt-Score", "y": ""},
        template=MS_LIGHT,
        color=values,
        color_continuous_scale=[
            [0.0, "#C2281E"],
            [0.5, "#CC8A1E"],
            [1.0, "#1B7F3A"],
        ],
        range_color=[20, 80],
    )
    if active_sector and active_sector in sectors:
        opacities = [1.0 if s == active_sector else 0.28 for s in sectors]
    else:
        opacities = [1.0] * len(sectors)
    fig.update_traces(
        marker_line_width=0,
        marker_opacity=opacities,
        hovertemplate="%{y}<br>Ø %{x:.1f}<extra></extra>",
    )
    fig.update_layout(
        height=420,
        margin=dict(l=8, r=16, t=16, b=32),
        coloraxis_showscale=False,
        clickmode="event",
        hovermode="y",
    )
    return fig


def _build_top50(df: pd.DataFrame, active_sector: str | None) -> pd.DataFrame:
    src = df if not active_sector else df[df["sector"] == active_sector]
    return (
        format_scored(src)
        .sort_values("total_score", ascending=False)
        .head(50)[TOP50_COLUMNS]
    )


def _filter_chip(active_sector: str | None) -> list:
    if not active_sector:
        return []
    return [
        html.Span(
            [
                html.Span("Filter", className="ms-badge-label"),
                html.Span(active_sector, className="ms-badge-value"),
                html.Button(
                    "×",
                    id="dash-sector-reset",
                    className="ms-badge-close",
                    n_clicks=0,
                    **{"aria-label": f"Filter {active_sector} entfernen"},
                ),
            ],
            className="ms-badge is-info",
        )
    ]


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return html.Div(
            [
                page_title(
                    "Multi-Faktor Dashboard",
                    "Meeder & Seifer Vermögensverwaltung",
                ),
                _empty_state(),
            ]
        )

    n_stocks = len(df)
    n_filter_ok = (df["filter_ok"] == "JA").sum()
    avg_score = df["total_score"].dropna().mean()
    n_strong = (df["recommendation"] == "STRONG BUY").sum()
    n_buy = (df["recommendation"] == "BUY").sum()
    n_hold = (df["recommendation"] == "HOLD").sum()

    kpis = kpi_band(
        [
            {"label": "Anzahl Aktien", "value": fmt_de(n_stocks, 0)},
            {
                "label": "Filter bestanden",
                "value": fmt_de(n_filter_ok, 0),
                "sub": fmt_percent(n_filter_ok / max(n_stocks, 1), 0),
            },
            {"label": "Ø Gesamt-Score", "value": fmt_de(avg_score, 1)},
            {"label": "Strong Buy", "value": fmt_de(n_strong, 0), "tone": "up"},
            {"label": "Buy", "value": fmt_de(n_buy, 0), "tone": "up"},
            {"label": "Hold", "value": fmt_de(n_hold, 0), "tone": "warn"},
        ]
    )

    avg_factors = {
        "Value": df["value_score"].dropna().mean(),
        "Quality": df["quality_score"].dropna().mean(),
        "Growth": df["growth_score"].dropna().mean(),
        "Momentum": df["momentum_score"].dropna().mean(),
        "Low Vol": df["lowvol_score"].dropna().mean(),
    }
    fig_factors = px.bar(
        x=list(avg_factors.keys()),
        y=list(avg_factors.values()),
        labels={"x": "", "y": "Ø Score"},
        template=MS_LIGHT,
    )
    fig_factors.update_traces(marker_color="#0B3D91", marker_line_width=0)
    fig_factors.update_layout(
        showlegend=False,
        height=260,
        margin=dict(l=40, r=16, t=16, b=36),
    )

    return html.Div(
        [
            page_title(
                "Multi-Faktor Dashboard",
                "Meeder & Seifer Vermögensverwaltung",
            ),
            kpis,
            section_header("Faktor-Scores im Universum"),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Graph(figure=fig_factors, config={"displayModeBar": False}),
                        md=5,
                    ),
                    dbc.Col(
                        dcc.Graph(
                            id="dash-sector",
                            figure=_build_sector_fig(df, None),
                            config={"displayModeBar": False},
                        ),
                        md=7,
                    ),
                ]
            ),
            dcc.Store(id="dash-sector-filter", data=None),
            section_header(
                "Top 50 Aktien nach Gesamt-Score",
                subtitle="Sortierbar · filterbar · Sektor-Balken zum Eingrenzen klicken",
            ),
            html.Div(
                id="dash-sector-chip",
                className="ms-badge-row mb-2",
                children=_filter_chip(None),
            ),
            html.Div(
                id="dash-top50-container",
                children=render_table(_build_top50(df, None), id="top50-table"),
            ),
        ]
    )


@callback(
    Output("dash-sector-filter", "data", allow_duplicate=True),
    Input("dash-sector", "clickData"),
    State("dash-sector-filter", "data"),
    prevent_initial_call=True,
)
def _on_sector_click(click_data, current):
    if not (click_data and click_data.get("points")):
        return no_update
    sector = click_data["points"][0].get("y")
    if sector == current:
        return None
    return sector


@callback(
    Output("dash-sector-filter", "data", allow_duplicate=True),
    Input("dash-sector-reset", "n_clicks"),
    prevent_initial_call=True,
)
def _on_sector_reset(_n_clicks):
    return None


@callback(
    Output("dash-sector", "figure"),
    Output("dash-sector-chip", "children"),
    Output("dash-top50-container", "children"),
    Input("dash-sector-filter", "data"),
    prevent_initial_call=True,
)
def _render_filtered(active_sector):
    df = STATE.scored
    if df.empty:
        return {}, [], ""
    fig = _build_sector_fig(df, active_sector)
    chip = _filter_chip(active_sector)
    table = render_table(_build_top50(df, active_sector), id="top50-table")
    return fig, chip, table


register_page(__name__, path="/", name="Dashboard", layout=layout)
