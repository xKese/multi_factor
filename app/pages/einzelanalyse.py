"""Einzelanalyse je Ticker (entspricht Sheet ``Einzelanalyse``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, register_page

from app.core.state import STATE


INDICATOR_GROUPS = {
    "Value": [
        ("P/B", "pb"),
        ("P/E", "pe"),
        ("P/FCF", "pfcf"),
        ("EV/EBITDA", "ev_ebitda"),
        ("P/S", "ps"),
        ("PEG", "peg"),
        ("Dividend Yield", "div_yield"),
    ],
    "Quality": [
        ("ROE", "roe"),
        ("ROIC", "roic"),
        ("ROA", "roa"),
        ("Gross Margin", "gross_margin"),
        ("Operating Margin", "op_margin"),
        ("Debt/Equity", "debt_equity"),
        ("Interest Coverage", "int_coverage"),
        ("Current Ratio", "current_ratio"),
        ("Piotroski", "piotroski"),
        ("Altman Z", "altman_z"),
    ],
    "Growth": [
        ("Revenue CAGR 3Y", "rev_cagr_3y"),
        ("EPS CAGR 3Y", "eps_cagr_3y"),
        ("FCF CAGR 3Y", "fcf_cagr_3y"),
        ("Forward EPS Growth", "fwd_eps_growth"),
    ],
    "Momentum": [
        ("Return 1M", "ret_1m"),
        ("Return 3M", "ret_3m"),
        ("Return 6M", "ret_6m"),
        ("Return 12M", "ret_12m"),
        ("EPS Revisions 3M", "eps_revisions_3m"),
    ],
    "Low Volatility": [
        ("Beta", "beta"),
        ("Volatilität 1Y", "volatility_1y"),
        ("52W Range %", "range_52w"),
    ],
}


def _kv_table(pairs: list[tuple[str, str]], row: pd.Series) -> dbc.Table:
    rows = []
    for label, col in pairs:
        val = row.get(col)
        if pd.isna(val):
            display = "-"
        elif isinstance(val, float):
            display = f"{val:,.3f}"
        else:
            display = str(val)
        rows.append(html.Tr([html.Td(label), html.Td(display)]))
    return dbc.Table(
        [html.Tbody(rows)],
        bordered=False,
        striped=True,
        hover=True,
        size="sm",
        className="mb-0",
    )


def layout(ticker: str = "", **_) -> html.Div:
    options = []
    if not STATE.scored.empty:
        options = [
            {"label": f"{t} – {n}", "value": t}
            for t, n in STATE.scored[["ticker", "name"]].head(2000).itertuples(index=False)
        ]

    return html.Div(
        [
            html.H2("Einzelanalyse"),
            dbc.Row(
                [
                    dbc.Col(
                        dcc.Dropdown(
                            id="ea-ticker",
                            options=options,
                            value=ticker or (options[0]["value"] if options else None),
                            placeholder="Ticker wählen …",
                            searchable=True,
                        ),
                        md=4,
                    ),
                ],
                className="mb-3",
            ),
            html.Div(id="ea-content"),
        ],
        className="p-4",
    )


@callback(Output("ea-content", "children"), Input("ea-ticker", "value"))
def _render(ticker: str | None):
    if not ticker or STATE.scored.empty:
        return dbc.Alert("Keine Daten verfügbar.", color="info")

    row = STATE.scored.loc[STATE.scored["ticker"] == ticker]
    if row.empty:
        return dbc.Alert(f"Ticker {ticker} nicht gefunden.", color="warning")
    r = row.iloc[0]

    header = dbc.Row(
        [
            dbc.Col(html.H3(f"{r['ticker']} – {r['name']}"), md=8),
            dbc.Col(
                html.Div(
                    [
                        html.H3(
                            f"{r['total_score']:.1f}"
                            if pd.notna(r["total_score"])
                            else "-",
                            className="mb-0 text-end",
                        ),
                        html.Small(r["classification"], className="text-muted"),
                    ]
                ),
                md=4,
            ),
        ],
        className="mb-3",
    )

    meta = dbc.Row(
        [
            dbc.Col(html.Div([html.Strong("Sektor: "), r["sector"] or "-"]), md=3),
            dbc.Col(html.Div([html.Strong("Industrie: "), r["industry"] or "-"]), md=3),
            dbc.Col(html.Div([html.Strong("Region: "), r["region"] or "-"]), md=3),
            dbc.Col(
                html.Div(
                    [
                        html.Strong("Market Cap: "),
                        f"{r['market_cap']:,.0f} Mio." if pd.notna(r["market_cap"]) else "-",
                    ]
                ),
                md=3,
            ),
        ],
        className="mb-3",
    )

    factors = {
        "Value": r["value_score"],
        "Quality": r["quality_score"],
        "Growth": r["growth_score"],
        "Momentum": r["momentum_score"],
        "Low Vol": r["lowvol_score"],
    }
    fig = go.Figure(
        go.Scatterpolar(
            r=[0 if pd.isna(v) else v for v in factors.values()],
            theta=list(factors.keys()),
            fill="toself",
            name=r["ticker"],
        )
    )
    fig.update_layout(
        polar=dict(radialaxis=dict(visible=True, range=[0, 100])),
        showlegend=False,
        height=360,
        title="Faktor-Scores",
    )

    filter_badges = dbc.Row(
        [
            dbc.Col(
                dbc.Alert(
                    f"Piotroski: {r['piotroski']:.0f} / 9"
                    if pd.notna(r["piotroski"])
                    else "Piotroski: -",
                    color="success"
                    if pd.notna(r["piotroski"]) and r["piotroski"] >= STATE.settings.min_piotroski
                    else "danger",
                ),
                md=3,
            ),
            dbc.Col(
                dbc.Alert(
                    f"Altman Z: {r['altman_z']:.2f}"
                    if pd.notna(r["altman_z"])
                    else "Altman Z: -",
                    color="success"
                    if pd.notna(r["altman_z"]) and r["altman_z"] >= STATE.settings.min_altman_z
                    else "danger",
                ),
                md=3,
            ),
            dbc.Col(
                dbc.Alert(
                    f"SMA-Signal: {r['sma_signal']}",
                    color="info",
                ),
                md=3,
            ),
            dbc.Col(
                dbc.Alert(
                    f"Empfehlung: {r['recommendation']}",
                    color={
                        "STRONG BUY": "success",
                        "BUY": "success",
                        "HOLD": "warning",
                        "SELL": "danger",
                    }.get(r["recommendation"], "secondary"),
                ),
                md=3,
            ),
        ],
        className="mb-3",
    )

    cards = []
    for factor, pairs in INDICATOR_GROUPS.items():
        cards.append(
            dbc.Col(
                dbc.Card(
                    [
                        dbc.CardHeader(factor),
                        dbc.CardBody(_kv_table(pairs, r)),
                    ],
                    className="mb-3 h-100",
                ),
                md=6,
            )
        )

    return html.Div(
        [
            header,
            meta,
            filter_badges,
            dbc.Row(
                [dbc.Col(dcc.Graph(figure=fig), md=6), dbc.Col(md=6)],
                className="mb-2",
            ),
            dbc.Row(cards),
        ]
    )


register_page(__name__, path="/einzelanalyse", name="Einzelanalyse", layout=layout)
