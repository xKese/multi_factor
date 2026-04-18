"""Gemeinsam genutzte UI-Helfer."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import dash_table, html


SCORE_COLORS = {
    "A - Exzellent": "#1b5e20",
    "B+ - Sehr Gut": "#2e7d32",
    "B - Gut": "#558b2f",
    "C - Durchschnitt": "#f9a825",
    "D - Unterdurchschnitt": "#ef6c00",
    "F - Schwach": "#c62828",
}


def kpi_card(label: str, value: str, color: str = "primary") -> dbc.Card:
    return dbc.Card(
        dbc.CardBody(
            [
                html.Small(label, className="text-muted"),
                html.H3(value, className=f"text-{color} mb-0"),
            ]
        ),
        className="shadow-sm",
    )


def render_table(
    df: pd.DataFrame,
    columns: list[dict] | None = None,
    id: str = "table",
    page_size: int = 25,
) -> dash_table.DataTable:
    if columns is None:
        columns = [{"name": c, "id": c} for c in df.columns]
    return dash_table.DataTable(
        id=id,
        data=df.to_dict("records"),
        columns=columns,
        sort_action="native",
        filter_action="native",
        page_action="native",
        page_size=page_size,
        style_table={"overflowX": "auto"},
        style_cell={
            "fontFamily": "system-ui",
            "fontSize": "13px",
            "padding": "6px 10px",
            "textAlign": "left",
        },
        style_header={
            "backgroundColor": "#1f2937",
            "color": "white",
            "fontWeight": "bold",
        },
        style_data_conditional=[
            {
                "if": {"filter_query": "{recommendation} = 'STRONG BUY'"},
                "backgroundColor": "#e8f5e9",
            },
            {
                "if": {"filter_query": "{recommendation} = 'BUY'"},
                "backgroundColor": "#f1f8e9",
            },
            {
                "if": {"filter_query": "{recommendation} = 'SELL'"},
                "backgroundColor": "#ffebee",
            },
        ],
    )


def format_scored(df: pd.DataFrame) -> pd.DataFrame:
    """Rundung für die Tabellen-Darstellung."""
    df = df.copy()
    for col in [
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
        "total_score",
    ]:
        if col in df.columns:
            df[col] = df[col].round(1)
    if "sma_200_distance" in df.columns:
        df["sma_200_distance"] = (df["sma_200_distance"] * 100).round(2)
    return df
