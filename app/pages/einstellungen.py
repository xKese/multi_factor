"""Einstellungs-Seite (entspricht Sheet ``Einstellungen``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, html, register_page

from app.core.state import STATE


FACTOR_FIELDS = [("value", "Value"), ("quality", "Quality"), ("growth", "Growth"),
                 ("momentum", "Momentum"), ("lowvol", "Low Volatility")]


def _weight_row(fid: str, label: str, value: float, step: float = 0.01) -> dbc.Row:
    return dbc.Row(
        [
            dbc.Col(html.Label(label, className="fw-bold"), md=4),
            dbc.Col(
                dbc.Input(
                    id={"type": "factor-weight", "index": fid},
                    type="number",
                    min=0,
                    max=1,
                    step=step,
                    value=value,
                ),
                md=3,
            ),
        ],
        className="mb-2",
    )


def _indicator_table(title: str, weights: dict[str, float], prefix: str) -> dbc.Card:
    rows = []
    for key, val in weights.items():
        rows.append(
            dbc.Row(
                [
                    dbc.Col(html.Span(key), md=6),
                    dbc.Col(
                        dbc.Input(
                            id={"type": "ind-weight", "index": f"{prefix}:{key}"},
                            type="number",
                            min=0,
                            max=1,
                            step=0.01,
                            value=val,
                            size="sm",
                        ),
                        md=4,
                    ),
                ],
                className="mb-1",
            )
        )
    return dbc.Card(
        [dbc.CardHeader(title), dbc.CardBody(rows)],
        className="mb-3",
    )


def layout(**_) -> html.Div:
    s = STATE.settings
    factor_rows = [
        _weight_row(fid, label, s.factor_weights[fid]) for fid, label in FACTOR_FIELDS
    ]

    filters = dbc.Card(
        [
            dbc.CardHeader("Filter-Kriterien"),
            dbc.CardBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Min. Piotroski F-Score"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="min-piotroski",
                                    type="number",
                                    value=s.min_piotroski,
                                    min=0,
                                    max=9,
                                    step=0.5,
                                ),
                                md=4,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Min. Altman Z-Score"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="min-altman",
                                    type="number",
                                    value=s.min_altman_z,
                                    step=0.1,
                                ),
                                md=4,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Min. Market Cap (Mio.)"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="min-mcap",
                                    type="number",
                                    value=s.min_market_cap,
                                    step=100,
                                ),
                                md=4,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Min. Aktien pro Industrie"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="min-industry",
                                    type="number",
                                    value=s.min_stocks_per_industry,
                                    min=1,
                                    step=1,
                                ),
                                md=4,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Perzentil-Modus"), md=6),
                            dbc.Col(
                                dbc.Select(
                                    id="pct-mode",
                                    options=[
                                        {"label": x, "value": x}
                                        for x in ["Global", "Sektor", "Industrie"]
                                    ],
                                    value=s.percentile_mode,
                                ),
                                md=4,
                            ),
                        ]
                    ),
                ]
            ),
        ],
        className="mb-3",
    )

    indicator_cards = dbc.Row(
        [
            dbc.Col(_indicator_table("Value-Indikatoren", s.value_weights, "value"), md=6),
            dbc.Col(_indicator_table("Quality-Indikatoren", s.quality_weights, "quality"), md=6),
            dbc.Col(_indicator_table("Growth-Indikatoren", s.growth_weights, "growth"), md=6),
            dbc.Col(_indicator_table("Momentum-Indikatoren", s.momentum_weights, "momentum"), md=6),
            dbc.Col(_indicator_table("Low Vol-Indikatoren", s.lowvol_weights, "lowvol"), md=6),
        ]
    )

    return html.Div(
        [
            html.H2("Einstellungen"),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Faktor-Gewichtungen"),
                                dbc.CardBody(factor_rows),
                            ]
                        ),
                        md=6,
                    ),
                    dbc.Col(filters, md=6),
                ],
                className="mb-3",
            ),
            indicator_cards,
            dbc.Button(
                "Speichern & neu berechnen",
                id="save-settings",
                color="primary",
                size="lg",
                className="mt-3",
            ),
            html.Div(id="settings-status", className="mt-3"),
        ],
        className="p-4",
    )


@callback(
    Output("settings-status", "children"),
    Input("save-settings", "n_clicks"),
    State({"type": "factor-weight", "index": "value"}, "value"),
    State({"type": "factor-weight", "index": "quality"}, "value"),
    State({"type": "factor-weight", "index": "growth"}, "value"),
    State({"type": "factor-weight", "index": "momentum"}, "value"),
    State({"type": "factor-weight", "index": "lowvol"}, "value"),
    State("min-piotroski", "value"),
    State("min-altman", "value"),
    State("min-mcap", "value"),
    State("min-industry", "value"),
    State("pct-mode", "value"),
    State({"type": "ind-weight", "index": ALL}, "value"),
    State({"type": "ind-weight", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def _save(n_clicks, v, q, g, m, lv, piotr, altman, mcap, minind, mode, ind_vals, ind_ids):
    if not n_clicks:
        return ""
    s = STATE.settings
    s.factor_weights = {
        "value": float(v or 0),
        "quality": float(q or 0),
        "growth": float(g or 0),
        "momentum": float(m or 0),
        "lowvol": float(lv or 0),
    }
    s.min_piotroski = float(piotr or 0)
    s.min_altman_z = float(altman or 0)
    s.min_market_cap = float(mcap or 0)
    s.min_stocks_per_industry = int(minind or 1)
    s.percentile_mode = mode or "Industrie"

    # Indikator-Gewichte schreiben
    groups = {
        "value": s.value_weights,
        "quality": s.quality_weights,
        "growth": s.growth_weights,
        "momentum": s.momentum_weights,
        "lowvol": s.lowvol_weights,
    }
    for val, ident in zip(ind_vals or [], ind_ids or [], strict=False):
        prefix, key = ident["index"].split(":", 1)
        if prefix in groups and key in groups[prefix]:
            groups[prefix][key] = float(val or 0)

    STATE.recompute()
    return dbc.Alert("Einstellungen gespeichert. Scores wurden neu berechnet.", color="success")


register_page(__name__, path="/einstellungen", name="Einstellungen", layout=layout)
