"""Portfolio-Analyse (entspricht Sheets ``M&S Portfolio`` & ``Mein Portfolio``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, dcc, html, register_page

from app.core.state import STATE
from app.pages.common import format_scored, kpi_card, render_table


PORTFOLIO_COLS = [
    "ticker",
    "name",
    "sector",
    "industry",
    "value_score",
    "quality_score",
    "growth_score",
    "momentum_score",
    "lowvol_score",
    "total_score",
    "classification",
    "filter_ok",
    "recommendation",
    "sma_signal",
]


def _portfolio_df(tickers: list[str]) -> pd.DataFrame:
    if STATE.scored.empty:
        return pd.DataFrame(columns=PORTFOLIO_COLS)
    df = STATE.scored[STATE.scored["ticker"].isin(tickers)]
    return format_scored(df)[PORTFOLIO_COLS]


def _kpis(df: pd.DataFrame) -> dbc.Row:
    if df.empty:
        avg, n_ok, n_strong, n_sell = "-", 0, 0, 0
    else:
        avg = f"{df['total_score'].dropna().mean():.1f}"
        n_ok = (df["filter_ok"] == "JA").sum()
        n_strong = (df["recommendation"].isin(["STRONG BUY", "BUY"])).sum()
        n_sell = (df["recommendation"] == "SELL").sum()
    return dbc.Row(
        [
            dbc.Col(kpi_card("Positionen", f"{len(df):,}"), md=3),
            dbc.Col(kpi_card("Filter OK", f"{n_ok}/{len(df)}"), md=3),
            dbc.Col(kpi_card("Ø Score", str(avg)), md=3),
            dbc.Col(
                kpi_card(
                    "Buy / Sell",
                    f"{n_strong} / {n_sell}",
                    color="success" if n_strong >= n_sell else "danger",
                ),
                md=3,
            ),
        ],
        className="mb-3",
    )


def _portfolio_block(title: str, store_id: str, input_id: str) -> dbc.Card:
    return dbc.Card(
        [
            dbc.CardHeader(title),
            dbc.CardBody(
                [
                    dbc.InputGroup(
                        [
                            dbc.Input(
                                id=input_id,
                                placeholder="Ticker, komma- oder leerzeichengetrennt",
                            ),
                            dbc.Button("Setzen", id=f"{input_id}-btn", color="primary"),
                        ],
                        className="mb-3",
                    ),
                    html.Div(id=f"{store_id}-kpis"),
                    html.Div(id=f"{store_id}-table"),
                ]
            ),
        ],
        className="mb-3",
    )


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.H2("Portfolios"),
            dcc.Store(id="ms-store", data=STATE.ms_portfolio),
            dcc.Store(id="my-store", data=STATE.my_portfolio),
            _portfolio_block("M&S Portfolio", "ms", "ms-input"),
            _portfolio_block("Mein Portfolio", "my", "my-input"),
        ],
        className="p-4",
    )


def _parse(text: str | None) -> list[str]:
    if not text:
        return []
    return [t.strip().upper() for t in text.replace(",", " ").split() if t.strip()]


def _bind(store_id: str, input_id: str, target_attr: str):
    @callback(
        Output(f"{store_id}-store", "data"),
        Input(f"{input_id}-btn", "n_clicks"),
        State(input_id, "value"),
        State(f"{store_id}-store", "data"),
        prevent_initial_call=True,
    )
    def _set(n_clicks, value, current):  # noqa: ARG001
        new = _parse(value)
        if not new:
            return current
        setattr(STATE, target_attr, new)
        return new

    @callback(
        Output(f"{store_id}-table", "children"),
        Output(f"{store_id}-kpis", "children"),
        Input(f"{store_id}-store", "data"),
    )
    def _render(tickers):
        df = _portfolio_df(tickers or [])
        return render_table(df, id=f"{store_id}-tbl"), _kpis(df)


_bind("ms", "ms-input", "ms_portfolio")
_bind("my", "my-input", "my_portfolio")


register_page(__name__, path="/portfolios", name="Portfolios", layout=layout)
