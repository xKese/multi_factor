"""SMA-Signal-Monitor (entspricht Sheet ``SMA_Signale``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, register_page

from app.core.state import STATE
from app.pages.common import format_scored, page_title, render_table
from app.ui import MS_LIGHT, fmt_de, kpi_band, section_header


PRIORITY = {
    "⚠ DEATH CROSS": 0,
    "▼ Kurs < SMA-200": 1,
    "● Kurs > SMA-200": 2,
    "✓ GOLDEN CROSS": 3,
}

BULLISH_SIGNALS = {"✓ GOLDEN CROSS", "● Kurs > SMA-200"}
BEARISH_SIGNALS = {"⚠ DEATH CROSS", "▼ Kurs < SMA-200"}

SIGNAL_COLORS = {
    "⚠ DEATH CROSS": "#C2281E",
    "▼ Kurs < SMA-200": "#CC8A1E",
    "● Kurs > SMA-200": "#4CAF6E",
    "✓ GOLDEN CROSS": "#1B7F3A",
}

SIGNAL_OPTIONS = [
    {"label": "Alle", "value": "ALL"},
    {"label": "⚠ Death", "value": "⚠ DEATH CROSS"},
    {"label": "▼ < SMA-200", "value": "▼ Kurs < SMA-200"},
    {"label": "● > SMA-200", "value": "● Kurs > SMA-200"},
    {"label": "✓ Golden", "value": "✓ GOLDEN CROSS"},
]

PORTFOLIO_OPTIONS = [
    {"label": "Gesamt", "value": "all"},
    {"label": "M&S", "value": "ms"},
    {"label": "Mein", "value": "my"},
]

SIGNAL_COLS = [
    "ticker",
    "name",
    "sector",
    "total_score",
    "filter_ok",
    "recommendation",
    "sma_signal",
    "last_price",
    "sma_50",
    "sma_200",
    "sma_50_distance",
    "sma_200_distance",
]

WATCH_THRESHOLD = 0.03  # 3 %: |SMA-50 − SMA-200| / SMA-200
WATCH_TOP_N = 20
WATCH_COLS = [
    "ticker",
    "name",
    "sector",
    "direction",
    "last_price",
    "sma_50",
    "sma_200",
    "sma_gap",
    "total_score",
    "recommendation",
]


def _apply_portfolio_lens(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    if lens == "ms":
        return df[df["ticker"].isin(STATE.ms_portfolio)]
    if lens == "my":
        return df[df["ticker"].isin(STATE.my_portfolio)]
    return df


def _build_signals(df: pd.DataFrame, signal: str, lens: str) -> pd.DataFrame:
    mask = df["sma_signal"].isin(PRIORITY.keys())
    if signal != "ALL":
        mask &= df["sma_signal"] == signal
    filtered = _apply_portfolio_lens(df.loc[mask], lens)
    signals = format_scored(filtered).copy()
    signals["priority"] = signals["sma_signal"].map(PRIORITY)
    signals = signals.sort_values(
        ["priority", "total_score"], ascending=[True, False]
    )
    cols = [c for c in SIGNAL_COLS if c in signals.columns]
    return signals[cols]


def _build_watchlist(df: pd.DataFrame, lens: str) -> pd.DataFrame:
    """Titel nahe an einem SMA-50 / SMA-200 Crossover."""
    filtered = _apply_portfolio_lens(df, lens)
    has_sma = (
        filtered["sma_50"].notna()
        & filtered["sma_200"].notna()
        & (filtered["sma_200"] > 0)
    )
    gap = (filtered["sma_50"] - filtered["sma_200"]) / filtered["sma_200"]
    mask = has_sma & (gap.abs() < WATCH_THRESHOLD)
    candidates = filtered.loc[mask].copy()
    if candidates.empty:
        return candidates
    raw_gap = (
        (candidates["sma_50"] - candidates["sma_200"]) / candidates["sma_200"]
    )
    candidates["sma_gap"] = raw_gap
    candidates["direction"] = np.where(
        raw_gap >= 0, "↑ Golden voraus", "↓ Death voraus"
    )
    candidates["abs_gap"] = raw_gap.abs()
    candidates = candidates.sort_values("abs_gap").head(WATCH_TOP_N)
    formatted = format_scored(candidates)
    cols = [c for c in WATCH_COLS if c in formatted.columns]
    return formatted[cols]


def _breadth_subtitle(df: pd.DataFrame) -> str:
    n_bullish = int(df["sma_signal"].isin(BULLISH_SIGNALS).sum())
    n_bearish = int(df["sma_signal"].isin(BEARISH_SIGNALS).sum())
    delta = n_bullish - n_bearish
    sign = "+" if delta >= 0 else "−"
    return (
        f"Marktbreite: {fmt_de(n_bullish, 0)} bullish · "
        f"{fmt_de(n_bearish, 0)} bearish · Δ{sign}{abs(delta)}"
    )


def _heatmap(df: pd.DataFrame, lens: str):
    """Gestapelte Sektor-Balken: Signal-Verteilung pro Sektor."""
    view = _apply_portfolio_lens(df, lens)
    view = view[view["sma_signal"].isin(PRIORITY)]
    if view.empty or "sector" not in view.columns:
        return dbc.Alert(
            "Keine Sektor-Daten für diese Auswahl.", color="secondary"
        )
    grid = (
        view.groupby(["sector", "sma_signal"]).size().unstack(fill_value=0)
    )
    zeros = pd.Series(0, index=grid.index)
    bear = grid.get("⚠ DEATH CROSS", zeros) + grid.get(
        "▼ Kurs < SMA-200", zeros
    )
    grid = grid.loc[bear.sort_values(ascending=True).index]

    fig = go.Figure()
    for sig in PRIORITY:
        if sig in grid.columns:
            fig.add_bar(
                y=grid.index.tolist(),
                x=grid[sig].tolist(),
                orientation="h",
                name=sig,
                marker_color=SIGNAL_COLORS[sig],
                hovertemplate="<b>%{y}</b><br>%{x} Titel<extra></extra>",
            )
    fig.update_layout(
        barmode="stack",
        template=MS_LIGHT,
        height=max(220, 24 * len(grid) + 120),
        margin=dict(l=8, r=16, t=8, b=90),
        legend=dict(
            orientation="h", yanchor="top", y=-0.25, x=0, xanchor="left"
        ),
        xaxis_title="Anzahl Titel",
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


def _watchlist_component(df: pd.DataFrame, lens: str):
    data = _build_watchlist(df, lens)
    if data.empty:
        return dbc.Alert(
            f"Keine Titel mit |SMA-50 − SMA-200| < {int(WATCH_THRESHOLD * 100)} %.",
            color="secondary",
        )
    return render_table(data, id="sma-watchlist-table", page_size=WATCH_TOP_N)


def _info_popover() -> html.Span:
    trigger = dbc.Button(
        "ℹ Definitionen",
        id="sma-info-btn",
        color="link",
        size="sm",
        className="text-decoration-none p-0",
        n_clicks=0,
    )
    popover = dbc.Popover(
        [
            dbc.PopoverHeader("Signal-Definitionen"),
            dbc.PopoverBody(
                [
                    html.Div(
                        [
                            html.Strong("✓ Golden Cross"),
                            " — Kurs > SMA-50 UND Kurs > SMA-200 (stark bullish).",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("● Kurs > SMA-200"),
                            " — Kurs über der 200-Tage-Linie, aber kein vollständiger Golden Cross.",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("▼ Kurs < SMA-200"),
                            " — Kurs unter der 200-Tage-Linie, aber kein vollständiger Death Cross.",
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        [
                            html.Strong("⚠ Death Cross"),
                            " — Kurs < SMA-50 UND Kurs < SMA-200 (stark bearish).",
                        ],
                    ),
                ]
            ),
        ],
        target="sma-info-btn",
        trigger="click",
        placement="right",
    )
    return html.Span([trigger, popover])


def _controls() -> html.Div:
    radio_classes = dict(
        inline=True,
        class_name="btn-group",
        input_class_name="btn-check",
        label_class_name="btn btn-sm btn-outline-secondary",
    )
    return html.Div(
        [
            html.Div(
                [
                    html.Small("Signal", className="text-muted d-block mb-1"),
                    dbc.RadioItems(
                        id="sma-signal-filter",
                        options=SIGNAL_OPTIONS,
                        value="ALL",
                        **radio_classes,
                    ),
                ],
                className="me-4 mb-2",
            ),
            html.Div(
                [
                    html.Small("Portfolio", className="text-muted d-block mb-1"),
                    dbc.RadioItems(
                        id="sma-portfolio-lens",
                        options=PORTFOLIO_OPTIONS,
                        value="all",
                        **radio_classes,
                    ),
                ],
                className="mb-2",
            ),
        ],
        className="d-flex flex-wrap align-items-start mb-3",
    )


def layout(**_) -> html.Div:
    df = STATE.scored
    if df.empty:
        return html.Div(
            [
                page_title("SMA-Signal-Monitor"),
                dbc.Alert("Keine Daten geladen.", color="info"),
            ]
        )

    n_golden = (df["sma_signal"] == "✓ GOLDEN CROSS").sum()
    n_death = (df["sma_signal"] == "⚠ DEATH CROSS").sum()
    n_below = (df["sma_signal"] == "▼ Kurs < SMA-200").sum()
    n_above = (df["sma_signal"] == "● Kurs > SMA-200").sum()

    kpis = kpi_band(
        [
            {"label": "Death Cross", "value": fmt_de(n_death, 0), "tone": "down"},
            {"label": "Kurs < SMA-200", "value": fmt_de(n_below, 0), "tone": "warn"},
            {"label": "Kurs > SMA-200", "value": fmt_de(n_above, 0)},
            {"label": "Golden Cross", "value": fmt_de(n_golden, 0), "tone": "up"},
        ]
    )

    initial_table = render_table(
        _build_signals(df, "ALL", "all"), id="sma-table", page_size=50
    )

    return html.Div(
        [
            page_title("SMA-Signal-Monitor", _breadth_subtitle(df)),
            kpis,
            section_header(
                "Sektor-Breite",
                subtitle="Signal-Verteilung pro Sektor — bearish zuerst",
            ),
            html.Div(id="sma-heatmap-container", children=_heatmap(df, "all")),
            html.Div(
                [section_header("Signale"), _info_popover()],
                className="d-flex align-items-center justify-content-between",
            ),
            _controls(),
            html.Div(id="sma-table-container", children=initial_table),
            section_header(
                "Nahe am Kreuz",
                subtitle=(
                    f"|SMA-50 − SMA-200| < {int(WATCH_THRESHOLD * 100)} % · "
                    f"Top {WATCH_TOP_N} nach Nähe · bevorstehende Kreuzungen"
                ),
            ),
            html.Div(
                id="sma-watchlist-container",
                children=_watchlist_component(df, "all"),
            ),
        ]
    )


@callback(
    Output("sma-table-container", "children"),
    Output("sma-heatmap-container", "children"),
    Output("sma-watchlist-container", "children"),
    Input("sma-signal-filter", "value"),
    Input("sma-portfolio-lens", "value"),
    prevent_initial_call=True,
)
def _render(signal: str | None, lens: str | None):
    df = STATE.scored
    if df.empty:
        empty = dbc.Alert("Keine Daten geladen.", color="info")
        return empty, empty, empty
    sig = signal or "ALL"
    ln = lens or "all"
    table_df = _build_signals(df, sig, ln)
    table = (
        dbc.Alert(
            "Keine Treffer für die gewählte Kombination.", color="secondary"
        )
        if table_df.empty
        else render_table(table_df, id="sma-table", page_size=50)
    )
    return table, _heatmap(df, ln), _watchlist_component(df, ln)


register_page(__name__, path="/sma", name="SMA-Signale", layout=layout)
