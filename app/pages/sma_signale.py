"""SMA-Signal-Monitor (entspricht Sheet ``SMA_Signale``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import html, register_page

from app.core.state import STATE
from app.pages.common import format_scored, kpi_card, render_table


PRIORITY = {
    "⚠ DEATH CROSS": 0,
    "▼ Kurs < SMA-200": 1,
    "● Kurs > SMA-200": 2,
    "✓ GOLDEN CROSS": 3,
}


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return dbc.Alert("Keine Daten geladen.", color="info", className="m-4")

    n_golden = (df["sma_signal"] == "✓ GOLDEN CROSS").sum()
    n_death = (df["sma_signal"] == "⚠ DEATH CROSS").sum()
    n_below = (df["sma_signal"] == "▼ Kurs < SMA-200").sum()
    n_above = (df["sma_signal"] == "● Kurs > SMA-200").sum()

    kpis = dbc.Row(
        [
            dbc.Col(kpi_card("Death Cross", f"{n_death:,}", color="danger"), md=3),
            dbc.Col(kpi_card("Kurs < SMA-200", f"{n_below:,}", color="warning"), md=3),
            dbc.Col(kpi_card("Kurs > SMA-200", f"{n_above:,}", color="info"), md=3),
            dbc.Col(kpi_card("Golden Cross", f"{n_golden:,}", color="success"), md=3),
        ],
        className="mb-4",
    )

    mask = df["sma_signal"].isin(PRIORITY.keys())
    signals = format_scored(df.loc[mask]).copy()
    signals["priority"] = signals["sma_signal"].map(PRIORITY)
    signals = signals.sort_values(
        ["priority", "total_score"], ascending=[True, False]
    )
    cols = [
        "ticker",
        "name",
        "sector",
        "total_score",
        "filter_ok",
        "recommendation",
        "sma_signal",
        "sma_200_distance",
    ]
    signals = signals[cols]

    return html.Div(
        [
            html.H2("SMA-Signal-Monitor"),
            html.P(
                "Alle Titel mit aktiven SMA-50 / SMA-200 Signalen. Nach Priorität sortiert.",
                className="text-muted",
            ),
            kpis,
            render_table(signals, id="sma-table", page_size=50),
        ],
        className="p-4",
    )


register_page(__name__, path="/sma", name="SMA-Signale", layout=layout)
