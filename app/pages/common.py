"""Gemeinsam genutzte UI-Helfer (Morningstar-Look).

Design-Rückführung: Tabellen tragen nur horizontale Hairlines (R1), die
3-px-Innenschiene ist der ausgewählten Zeile vorbehalten (R4), technische
Schlüssel wie ``uid`` bleiben unsichtbar (R8).
"""

from __future__ import annotations

import pandas as pd
from dash import dash_table, html
from dash.dash_table.Format import Format, Scheme, Sign, Symbol

from app.ui import PERCENT_FIELDS, label_for


SCORE_COLORS = {
    "A - Exzellent": "#1E6F2A",
    "B+ - Sehr Gut": "#358848",
    "B - Gut": "#6A9E2F",
    "C - Durchschnitt": "#CC8A1E",
    "D - Unterdurchschnitt": "#D76A15",
    "F - Schwach": "#A8281F",
}

# Klassifikation v2 nutzt Kurzformen ("A", "B+", …, siehe classify_v2) —
# gleiche Farbwerte wie v1.
SCORE_COLORS_V2 = {
    "A": "#1E6F2A",
    "B+": "#358848",
    "B": "#6A9E2F",
    "C": "#CC8A1E",
    "D": "#D76A15",
    "F": "#A8281F",
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
    "composite_score",
}

# Z-Score-Spalten (zwei Nachkommastellen, vorzeichenbehaftet).
_Z_COLS: set[str] = {
    "composite_z",
    "composite_raw",
    "z_value",
    "z_quality",
    "z_momentum",
    "z_investment",
}

# Technische Schlüssel: bleiben im DataFrame (Link-Ziele), verschwinden
# aber aus der Ansicht (R8).
_HIDDEN_COLS: set[str] = {"uid"}


def kpi_card(label: str, value: str, color: str = "primary") -> html.Div:
    """Einzelne KPI-Zelle im Morningstar-Stil (Rückwärtskompat.)."""
    tone_map = {
        "success": "is-up",
        "danger": "is-down",
        "warning": "is-warn",
    }
    value_cls = "ms-kpi-value " + tone_map.get(color, "")
    # Kein Inline-Rahmen: .ms-kpi-cell bringt Trennlinie und Fläche mit,
    # sonst zerfällt das durchgehende KPI-Band in Einzelkästchen (R3).
    return html.Div(
        [
            html.Div(label, className="ms-kpi-label"),
            html.Div(value, className=value_cls.strip()),
        ],
        className="ms-kpi-cell",
    )


def _column_def(col: str) -> dict:
    """Erzeugt die dash_table-Spaltendefinition inkl. Label + Format."""
    col_def: dict = {"name": label_for(col), "id": col}
    if col == "ticker":
        # Ticker-Zellen werden zu Links auf die Einzelanalyse.
        col_def["presentation"] = "markdown"
        return col_def
    if col == "delta_w":
        # Gewichtsänderung: Dezimalanteil, vorzeichenbehaftet (+1,25 %).
        col_def["type"] = "numeric"
        col_def["format"] = Format(
            precision=2,
            scheme=Scheme.percentage_rounded,
            sign=Sign.positive,
            nully="-",
        )
    elif col in {"weight_current", "weight_model", "weight_effective",
                 "weight_target"}:
        # Portfoliogewichte mit zwei Nachkommastellen (0,0350 = 3,50 %).
        col_def["type"] = "numeric"
        col_def["format"] = Format(
            precision=2,
            scheme=Scheme.percentage_rounded,
            nully="-",
        )
    elif col in PERCENT_FIELDS:
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
    elif col in _Z_COLS:
        col_def["type"] = "numeric"
        col_def["format"] = Format(
            precision=2, scheme=Scheme.fixed, sign=Sign.positive, nully="-"
        )
    elif col == "cte":
        col_def["type"] = "numeric"
        col_def["format"] = Format(precision=3, scheme=Scheme.fixed, nully="-")
    elif col == "adv_3m":
        col_def["type"] = "numeric"
        col_def["format"] = Format(precision=0, scheme=Scheme.fixed, nully="-")
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
        # ``uid`` bleibt im Frame (Link-Ziel), erscheint aber nicht als
        # eigene Spalte (R8) — allerdings nur, wenn ein ``ticker`` die
        # Identität der Zeile sichtbar trägt. Tabellen, die den Titel
        # ausschließlich über die uid ausweisen (Trade-Liste,
        # historisiertes Zielportfolio), behalten die Spalte, sonst
        # bliebe die Zeile namenlos.
        hidden = _HIDDEN_COLS if "ticker" in df.columns else set()
        columns = [_column_def(c) for c in df.columns if c not in hidden]

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

    # Tonwerte tragen ausschließlich Textfarbe (R4). Die frühere
    # 3-px-Innenschiene je BUY-/KANDIDAT-Zeile ist entfallen: sie lag auf
    # nahezu jeder Zeile und blockierte die Gold-Schiene der Auswahl.
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
    ]

    # Zone v2 (KANDIDAT/HALTEN/VERKAUFEN/FILTER) — greift nur, wenn die
    # Spalte in der Tabelle vorhanden ist.
    conditional += [
        {
            "if": {
                "filter_query": "{zone_v2} = 'KANDIDAT'",
                "column_id": "zone_v2",
            },
            "color": "var(--ms-up)",
            "fontWeight": "600",
        },
        {
            "if": {
                "filter_query": "{zone_v2} = 'HALTEN'",
                "column_id": "zone_v2",
            },
            "color": "var(--ms-warn)",
            "fontWeight": "500",
        },
        {
            "if": {
                "filter_query": "{zone_v2} = 'VERKAUFEN'",
                "column_id": "zone_v2",
            },
            "color": "var(--ms-down)",
            "fontWeight": "600",
        },
        {
            "if": {
                "filter_query": "{zone_v2} = 'FILTER'",
                "column_id": "zone_v2",
            },
            "color": "var(--ms-text-muted)",
        },
    ]

    # Klassifikation v2 (Kurzformen "A" … "F").
    conditional += [
        {
            "if": {
                "filter_query": f'{{classification_v2}} = "{cls}"',
                "column_id": "classification_v2",
            },
            "color": color,
            "fontWeight": "600",
        }
        for cls, color in SCORE_COLORS_V2.items()
    ]

    # Trade-Aktionen der Portfoliokonstruktion.
    _ACTION_TONES = {
        "BUY": ("var(--ms-up)", "600"),
        "INCREASE": ("var(--ms-up)", "500"),
        "SELL": ("var(--ms-down)", "600"),
        "REDUCE": ("var(--ms-down)", "500"),
        "DEFERRED": ("var(--ms-warn)", "500"),
        "HOLD": ("var(--ms-text-muted)", "400"),
    }
    conditional += [
        {
            "if": {
                "filter_query": f"{{action}} = '{action}'",
                "column_id": "action",
            },
            "color": color,
            "fontWeight": weight,
        }
        for action, (color, weight) in _ACTION_TONES.items()
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
        # Nur horizontale Hairlines — alle vier Kanten explizit, weil ein
        # pauschales "border": "none" die Vertikalen nicht zuverlässig
        # entfernt (R1).
        style_cell={
            "fontFamily": 'Inter, "Helvetica Neue", system-ui, sans-serif',
            "fontSize": "12px",
            "padding": "0 12px",
            "height": "36px",
            "textAlign": "left",
            "borderTop": "none",
            "borderLeft": "none",
            "borderRight": "none",
            "borderBottom": "1px solid var(--ms-border)",
            "backgroundColor": "var(--ms-bg)",
            "color": "var(--ms-text)",
        },
        style_cell_conditional=[
            {"if": {"column_type": "numeric"}, "textAlign": "right"},
        ],
        style_header={
            "backgroundColor": "var(--ms-surface-alt)",
            "color": "var(--ms-text-muted)",
            "fontWeight": "600",
            "fontSize": "9px",
            "textTransform": "uppercase",
            "letterSpacing": "0.18em",
            "padding": "10px 12px",
            "borderTop": "none",
            "borderLeft": "none",
            "borderRight": "none",
            "borderBottom": "1px solid var(--ms-border)",
        },
        style_filter={
            "backgroundColor": "var(--ms-surface)",
            "borderLeft": "none",
            "borderRight": "none",
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
                "rule": (
                    "color: var(--ms-accent); font-weight: 700; "
                    "letter-spacing: 0.02em; text-decoration: none;"
                ),
            },
            {
                "selector": ".dash-cell.column-id-ticker a:hover",
                "rule": (
                    "color: var(--ms-gold); text-decoration: underline; "
                    "text-underline-offset: 3px;"
                ),
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
    """Einheitlicher Seiten-Titel oben auf jeder Page (Serif, R5)."""
    children: list = [html.H1(title, className="ms-page-title")]
    if subtitle:
        children.append(html.Div(subtitle, className="ms-page-subtitle"))
    return html.Div(children)


def render_basic_table(df: pd.DataFrame) -> html.Table:
    """Kompakte HTML-Tabelle im Design-Stil: Hairlines, kein Zebra (R1).

    Ersetzt das frühere ``dbc.Table(striped=True, hover=True)``, dessen
    Bootstrap-Zebra dem Hairline-Prinzip widerspricht.
    """
    numeric = {c for c in df.columns if pd.api.types.is_numeric_dtype(df[c])}
    head = html.Thead(
        html.Tr(
            [
                html.Th(str(c), className="num" if c in numeric else None)
                for c in df.columns
            ]
        )
    )
    body = html.Tbody(
        [
            html.Tr(
                [
                    html.Td(
                        "-" if pd.isna(v) else str(v),
                        className="num" if c in numeric else None,
                    )
                    for c, v in zip(df.columns, row)
                ]
            )
            for row in df.itertuples(index=False, name=None)
        ]
    )
    return html.Table([head, body], className="ms-table")
