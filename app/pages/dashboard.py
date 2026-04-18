"""Dashboard-Seite (entspricht Sheet ``Dashboard``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import plotly.express as px
from dash import dcc, html, register_page

from app.core.state import STATE
from app.pages.common import format_scored, kpi_card, render_table


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return dbc.Alert(
            "Noch keine Daten geladen. Bitte im Tab Daten-Import einen Koyfin-CSV hochladen.",
            color="warning",
            className="m-4",
        )

    n_stocks = len(df)
    n_filter_ok = (df["filter_ok"] == "JA").sum()
    avg_score = df["total_score"].dropna().mean()
    n_strong = (df["recommendation"] == "STRONG BUY").sum()
    n_buy = (df["recommendation"] == "BUY").sum()
    n_hold = (df["recommendation"] == "HOLD").sum()

    kpi_row = dbc.Row(
        [
            dbc.Col(kpi_card("Anzahl Aktien", f"{n_stocks:,}"), md=2),
            dbc.Col(kpi_card("Filter bestanden", f"{n_filter_ok:,}"), md=2),
            dbc.Col(kpi_card("Ø Gesamt-Score", f"{avg_score:,.1f}"), md=2),
            dbc.Col(kpi_card("Strong Buy", f"{n_strong:,}", color="success"), md=2),
            dbc.Col(kpi_card("Buy", f"{n_buy:,}", color="success"), md=2),
            dbc.Col(kpi_card("Hold", f"{n_hold:,}", color="warning"), md=2),
        ],
        className="mb-4",
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
        labels={"x": "Faktor", "y": "Ø Score"},
        title="Durchschnittliche Faktor-Scores",
        color=list(avg_factors.keys()),
    )
    fig_factors.update_layout(showlegend=False, height=320)

    sector_avg = (
        df.dropna(subset=["total_score"])
        .groupby("sector")["total_score"]
        .mean()
        .sort_values(ascending=False)
    )
    fig_sector = px.bar(
        x=sector_avg.values,
        y=sector_avg.index,
        orientation="h",
        labels={"x": "Ø Gesamt-Score", "y": "Sektor"},
        title="Ø Score nach Sektor",
    )
    fig_sector.update_layout(height=420)

    top50 = (
        format_scored(df)
        .sort_values("total_score", ascending=False)
        .head(50)[
            [
                "ticker",
                "name",
                "sector",
                "industry",
                "region",
                "total_score",
                "classification",
                "recommendation",
            ]
        ]
    )

    return html.Div(
        [
            html.H2("Multi-Faktor Dashboard", className="mb-1"),
            html.P("Meeder & Seifer Vermögensverwaltung", className="text-muted"),
            kpi_row,
            dbc.Row(
                [
                    dbc.Col(dcc.Graph(figure=fig_factors), md=5),
                    dbc.Col(dcc.Graph(figure=fig_sector), md=7),
                ],
                className="mb-4",
            ),
            html.H4("Top 50 Aktien nach Gesamt-Score"),
            render_table(top50, id="top50-table"),
        ],
        className="p-4",
    )




register_page(__name__, path="/", name="Dashboard", layout=layout)
