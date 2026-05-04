"""Factor-Timing-Modul (entspricht Sheet ``Factor_Timing``).

Regelbasierte taktische Faktor-Allokation.
"""

from __future__ import annotations

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, register_page

from app.pages.common import page_title
from app.ui import MS_LIGHT, fmt_signed_percent, fmt_percent, ms_badge, section_header


STRATEGIC = {"Value": 0.2375, "Quality": 0.2375, "Growth": 0.2375,
             "Momentum": 0.2375, "Low Volatility": 0.05}

REGIME_MATRIX = {
    "GOLDILOCKS": {"Value": 1, "Quality": 0, "Growth": 1, "Momentum": 1, "Low Volatility": -1},
    "SLOWDOWN":   {"Value": -1, "Quality": 1, "Growth": -1, "Momentum": 0, "Low Volatility": 1},
    "STAGFLATION": {"Value": 0, "Quality": 1, "Growth": -1, "Momentum": -1, "Low Volatility": 1},
    "HEATING UP":  {"Value": 1, "Quality": 0, "Growth": 0, "Momentum": 1, "Low Volatility": -1},
}


def _detect_regime(pmi: float, pmi_trend: float, cli: float, inflation: float) -> str:
    if pmi > 50 and pmi_trend > 0 and cli > 0:
        return "GOLDILOCKS"
    if pmi < 50 and pmi_trend < 0:
        return "SLOWDOWN"
    if pmi < 50 and inflation > 3:
        return "STAGFLATION"
    return "HEATING UP"


def _input(label: str, input_id: str, value: float, step: float = 0.1) -> dbc.Row:
    return dbc.Row(
        [
            dbc.Col(html.Label(label), md=7),
            dbc.Col(
                dbc.Input(id=input_id, type="number", value=value, step=step, size="sm"),
                md=5,
            ),
        ],
        className="mb-2",
    )


def layout(**_) -> html.Div:
    return html.Div(
        [
            page_title(
                "Factor Timing System",
                "Regelbasierte taktische Faktor-Allokation — 6-Monats-Horizont.",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Makro-Signale"),
                                dbc.CardBody(
                                    [
                                        _input("ISM Manufacturing PMI", "ft-pmi", 48.0),
                                        _input("ISM PMI Trend (MoM)", "ft-pmi-trend", -3.2),
                                        _input("OECD CLI (YoY %)", "ft-cli", -2.9),
                                        _input("US 10Y-2Y Spread (bps)", "ft-spread", 0.5),
                                        _input("Inflation (CPI YoY %)", "ft-cpi", 3.3),
                                    ]
                                ),
                            ]
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Sentiment-Signale"),
                                dbc.CardBody(
                                    [
                                        _input("VIX Index", "ft-vix", 31.0),
                                        _input("Credit Spread (OAS bps)", "ft-credit", 400.0),
                                        _input("Put/Call Ratio", "ft-pcr", 0.96, step=0.01),
                                        _input("Fund Flows (Mrd.)", "ft-flows", -9.9),
                                    ]
                                ),
                            ]
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Factor Momentum 6M"),
                                dbc.CardBody(
                                    [
                                        _input("Value", "ft-mom-value", 21.6),
                                        _input("Quality", "ft-mom-quality", 5.7),
                                        _input("Growth", "ft-mom-growth", -1.7),
                                        _input("Momentum", "ft-mom-momentum", 10.2),
                                        _input("Low Vol", "ft-mom-lowvol", 1.0),
                                    ]
                                ),
                            ]
                        ),
                        md=4,
                    ),
                ],
                className="mb-3",
            ),
            html.Div(id="ft-output"),
        ]
    )


@callback(
    Output("ft-output", "children"),
    Input("ft-pmi", "value"),
    Input("ft-pmi-trend", "value"),
    Input("ft-cli", "value"),
    Input("ft-spread", "value"),
    Input("ft-cpi", "value"),
    Input("ft-vix", "value"),
    Input("ft-credit", "value"),
    Input("ft-pcr", "value"),
    Input("ft-flows", "value"),
    Input("ft-mom-value", "value"),
    Input("ft-mom-quality", "value"),
    Input("ft-mom-growth", "value"),
    Input("ft-mom-momentum", "value"),
    Input("ft-mom-lowvol", "value"),
)
def _compute(pmi, pmi_trend, cli, spread, cpi, vix, credit, pcr, flows,
             v, q, g, m, lv):
    pmi = float(pmi or 0)
    pmi_trend = float(pmi_trend or 0)
    cli = float(cli or 0)
    spread = float(spread or 0)
    cpi = float(cpi or 0)
    regime = _detect_regime(pmi, pmi_trend, cli, cpi)

    momentum = {
        "Value": float(v or 0),
        "Quality": float(q or 0),
        "Growth": float(g or 0),
        "Momentum": float(m or 0),
        "Low Volatility": float(lv or 0),
    }
    ranks = sorted(momentum, key=lambda k: momentum[k], reverse=True)
    mom_signal = {
        f: ("ÜBERGEWICHTEN" if ranks.index(f) < 2
            else "UNTERGEWICHTEN" if ranks.index(f) >= 3 else "NEUTRAL")
        for f in momentum
    }

    # Regime-Tilt + Momentum-Overlay kombinieren → Anpassung in ±10 %.
    tactical = {}
    for factor, strat in STRATEGIC.items():
        regime_tilt = REGIME_MATRIX[regime][factor] * 0.04
        mom_tilt = (
            0.03
            if mom_signal[factor] == "ÜBERGEWICHTEN"
            else (-0.03 if mom_signal[factor] == "UNTERGEWICHTEN" else 0)
        )
        sentiment_tilt = 0.0
        if factor == "Low Volatility" and float(vix or 0) > 25:
            sentiment_tilt += 0.02
        if factor == "Value" and float(credit or 0) > 500:
            sentiment_tilt -= 0.01
        tactical[factor] = max(0.05, min(0.45, strat + regime_tilt + mom_tilt + sentiment_tilt))

    s = sum(tactical.values())
    tactical = {k: v / s for k, v in tactical.items()}

    fig = go.Figure()
    fig.add_bar(
        x=list(STRATEGIC.keys()),
        y=list(STRATEGIC.values()),
        name="Strategisch",
        marker_color="#6B6B68",
    )
    fig.add_bar(
        x=list(tactical.keys()),
        y=list(tactical.values()),
        name="Taktisch",
        marker_color="#0B3D91",
    )
    fig.update_layout(
        template=MS_LIGHT,
        barmode="group",
        yaxis_tickformat=".0%",
        height=340,
        margin=dict(l=48, r=16, t=24, b=32),
    )

    regime_tone = {
        "GOLDILOCKS": "up",
        "HEATING UP": "warn",
        "SLOWDOWN": "down",
        "STAGFLATION": "down",
    }.get(regime)
    regime_badge = html.Div(
        ms_badge("Aktuelles Regime", regime, tone=regime_tone),
        className="ms-badge-row",
    )
    table = dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [html.Th(x) for x in ["Faktor", "Strategisch", "Taktisch", "Δ", "Momentum-Signal"]]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(f),
                            html.Td(fmt_percent(STRATEGIC[f], 1)),
                            html.Td(fmt_percent(tactical[f], 1)),
                            html.Td(fmt_signed_percent(tactical[f] - STRATEGIC[f], 1)),
                            html.Td(mom_signal[f]),
                        ]
                    )
                    for f in STRATEGIC
                ]
            ),
        ],
        bordered=True,
        striped=True,
        hover=True,
    )

    return html.Div(
        [
            regime_badge,
            section_header("Allokation", subtitle=f"Regime: {regime}"),
            dcc.Graph(figure=fig, config={"displayModeBar": False}),
            table,
        ]
    )


register_page(__name__, path="/factor-timing", name="Factor Timing", layout=layout)
