"""Gemeinsam genutzte UI-Helfer (Morningstar-Look)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import dash_table, html
from dash.dash_table.Format import Format, Scheme, Symbol

from app.ui import PERCENT_FIELDS, label_for


SCORE_COLORS = {
    "A - Exzellent": "#1E6F2A",
    "B+ - Sehr Gut": "#358848",
    "B - Gut": "#6A9E2F",
    "C - Durchschnitt": "#CC8A1E",
    "D - Unterdurchschnitt": "#D76A15",
    "F - Schwach": "#A8281F",
}


# Spalten, die bereits im DataFrame in Prozent-Notation vorliegen (× 100,
# z. B. ``sma_200_distance`` nach :func:`format_scored`).
_PRE_MULTIPLIED_PCT: set[str] = {
    "sma_200_distance",
    "sma_50_distance",
    "sma_20_distance",
    "sma_gap",
    "mom_12_1",
    "dist_52w_high",
}

# Score-Spalten (0–100, eine Nachkommastelle).
_SCORE_COLS: set[str] = {
    "value_score",
    "quality_score",
    "growth_score",
    "momentum_score",
    "lowvol_score",
    "total_score",
}


def kpi_card(label: str, value: str, color: str = "primary") -> html.Div:
    """Einzelne KPI-Zelle im Morningstar-Stil (Rückwärtskompat.)."""
    tone_map = {
        "success": "is-up",
        "danger": "is-down",
        "warning": "is-warn",
    }
    value_cls = "ms-kpi-value " + tone_map.get(color, "")
    return html.Div(
        [
            html.Div(label, className="ms-kpi-label"),
            html.Div(value, className=value_cls.strip()),
        ],
        className="ms-kpi-cell",
        style={
            "border": "1px solid var(--ms-border)",
            "borderRadius": "4px",
            "background": "var(--ms-bg)",
        },
    )


def _column_def(col: str) -> dict:
    """Erzeugt die dash_table-Spaltendefinition inkl. Label + Format."""
    col_def: dict = {"name": label_for(col), "id": col}
    if col == "ticker":
        # Ticker-Zellen werden zu Links auf die Einzelanalyse.
        col_def["presentation"] = "markdown"
        return col_def
    if col in PERCENT_FIELDS:
        # Werte sind Dezimalanteile (0,755 = 75,5 %).
        col_def["type"] = "numeric"
        col_def["format"] = Format(
            precision=1,
            scheme=Scheme.percentage_rounded,
            nully="-",
        )
    elif col in _PRE_MULTIPLIED_PCT:
        # Werte liegen bereits in ``%``-Notation (z. B. 5,23 = 5,23 %).
        col_def["type"] = "numeric"
        col_def["format"] = (
            Format(precision=2, scheme=Scheme.fixed, nully="-")
            .symbol(Symbol.yes)
            .symbol_suffix(" %")
        )
    elif col == "market_cap":
        col_def["type"] = "numeric"
        col_def["format"] = (
            Format(precision=0, scheme=Scheme.fixed, nully="-")
            .symbol(Symbol.yes)
            .symbol_suffix(" Mio.")
        )
    elif col in _SCORE_COLS:
        col_def["type"] = "numeric"
        col_def["format"] = Format(precision=1, scheme=Scheme.fixed, nully="-")
    elif col in {"last_price", "high_52w", "low_52w", "sma_50", "sma_200"}:
        col_def["type"] = "numeric"
        col_def["format"] = Format(precision=2, scheme=Scheme.fixed, nully="-")
    elif col == "anzahl":
        col_def["type"] = "numeric"
        col_def["format"] = Format(precision=0, scheme=Scheme.fixed, nully="-")
    return col_def


def render_table(
    df: pd.DataFrame,
    columns: list[dict] | None = None,
    id: str = "table",
    page_size: int = 25,
) -> dash_table.DataTable:
    if columns is None:
        columns = [_column_def(c) for c in df.columns]

    # Ticker-Spalte zu Markdown-Link auf /einzelanalyse?ticker=<uid> —
    # Link-Ziel ist die uid (eindeutig bei Ticker-Kollisionen), sichtbar
    # bleibt der Ticker.
    if "ticker" in df.columns and any(
        c.get("id") == "ticker" and c.get("presentation") == "markdown"
        for c in columns
    ):
        df = df.copy()
        targets = df["uid"] if "uid" in df.columns else df["ticker"]
        df["ticker"] = [
            (
                f"[{t}](/einzelanalyse?ticker={u if isinstance(u, str) and u else t})"
                if isinstance(t, str) and t
                else t
            )
            for t, u in zip(df["ticker"], targets)
        ]

    conditional = [
        {
            "if": {
                "filter_query": "{recommendation} = 'STRONG BUY'",
                "column_id": "recommendation",
            },
            "color": "var(--ms-up)",
            "fontWeight": "600",
        },
        {
            "if": {
                "filter_query": "{recommendation} = 'BUY'",
                "column_id": "recommendation",
            },
            "color": "var(--ms-up)",
            "fontWeight": "500",
        },
        {
            "if": {
                "filter_query": "{recommendation} = 'HOLD'",
                "column_id": "recommendation",
            },
            "color": "var(--ms-warn)",
            "fontWeight": "500",
        },
        {
            "if": {
                "filter_query": "{recommendation} = 'SELL'",
                "column_id": "recommendation",
            },
            "color": "var(--ms-down)",
            "fontWeight": "600",
        },
        {
            "if": {"filter_query": "{recommendation} = 'STRONG BUY'"},
            "borderLeft": "3px solid var(--ms-up)",
        },
        {
            "if": {"filter_query": "{recommendation} = 'SELL'"},
            "borderLeft": "3px solid var(--ms-down)",
        },
    ]

    return dash_table.DataTable(
        id=id,
        data=df.to_dict("records"),
        columns=columns,
        sort_action="native",
        filter_action="native",
        page_action="native",
        page_size=page_size,
        export_format="xlsx",
        export_headers="display",
        export_columns="visible",
        # Deutsche Zahlen: ``1.234,56``
        locale_format={"decimal": ",", "group": ".", "grouping": [3]},
        style_table={"overflowX": "auto", "border": "none"},
        style_cell={
            "fontFamily": 'Inter, "Helvetica Neue", system-ui, sans-serif',
            "fontSize": "13px",
            "padding": "8px 12px",
            "textAlign": "left",
            "border": "none",
            "borderBottom": "1px solid var(--ms-border)",
            "backgroundColor": "var(--ms-bg)",
            "color": "var(--ms-text)",
        },
        style_cell_conditional=[
            {"if": {"column_type": "numeric"}, "textAlign": "right"},
        ],
        style_header={
            "backgroundColor": "var(--ms-surface)",
            "color": "var(--ms-text-muted)",
            "fontWeight": "600",
            "fontSize": "10px",
            "textTransform": "uppercase",
            "letterSpacing": "0.06em",
            "border": "none",
            "borderBottom": "1px solid var(--ms-border)",
        },
        style_filter={
            "backgroundColor": "var(--ms-surface-alt)",
            "borderBottom": "1px solid var(--ms-border)",
        },
        style_data={
            "backgroundColor": "var(--ms-bg)",
            "color": "var(--ms-text)",
        },
        style_data_conditional=conditional,
        markdown_options={"link_target": "_self"},
        css=[
            {
                "selector": ".dash-cell-value",
                "rule": "font-variant-numeric: tabular-nums;",
            },
            {
                "selector": ".dash-cell.column-id-ticker a",
                "rule": "color: var(--ms-accent); font-weight: 600; text-decoration: none;",
            },
            {
                "selector": ".dash-cell.column-id-ticker a:hover",
                "rule": "text-decoration: underline;",
            },
        ],
    )


def format_scored(df: pd.DataFrame) -> pd.DataFrame:
    """Nur Score-Rundung. Prozent-Spalten bleiben als Dezimalanteile,
    die Darstellung erfolgt über :func:`_column_def` mit passendem Format.
    """
    df = df.copy()
    for col in _SCORE_COLS:
        if col in df.columns:
            df[col] = df[col].round(1)
    for col in _PRE_MULTIPLIED_PCT:
        if col in df.columns:
            # In Prozent-Punkt-Einheit (× 100) für das pre-multiplied Format.
            df[col] = (df[col] * 100).round(2)
    return df


def page_title(title: str, subtitle: str | None = None) -> html.Div:
    """Einheitlicher Seiten-Titel oben auf jeder Page."""
    children: list = [html.H1(title, className="ms-page-title")]
    if subtitle:
        children.append(html.Div(subtitle, className="ms-page-subtitle"))
    return html.Div(children)


def render_basic_table(df: pd.DataFrame) -> dbc.Table:
    """Kompakte HTML-Tabelle (z. B. für Info-Zusammenfassungen)."""
    return dbc.Table.from_dataframe(
        df, striped=True, hover=True, size="sm", className="mb-0"
    )
