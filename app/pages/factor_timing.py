"""Factor-Timing-Modul (entspricht Sheet ``Factor_Timing``).

Regelbasierte taktische Faktor-Allokation.
"""

from __future__ import annotations

import logging

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, no_update, register_page

from app.core.persistence import load_factor_timing_inputs, save_factor_timing_inputs
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import MS_LIGHT, fmt_signed_percent, fmt_percent, ms_badge, section_header


log = logging.getLogger(__name__)


# Fallback, falls Settings keine factor_weights enthalten (kann nicht passieren,
# da Settings einen Default-Factory hat, aber defensiv).
_STRATEGIC_FALLBACK: dict[str, float] = {
    "Value": 0.2375,
    "Quality": 0.2375,
    "Growth": 0.2375,
    "Momentum": 0.2375,
    "Low Volatility": 0.05,
}

# Mapping Settings-Key → Anzeige-Key im Factor-Timing-System.
_SETTINGS_TO_STRATEGIC: dict[str, str] = {
    "value": "Value",
    "quality": "Quality",
    "growth": "Growth",
    "momentum": "Momentum",
    "lowvol": "Low Volatility",
}

REGIME_MATRIX = {
    "GOLDILOCKS": {"Value": 1, "Quality": 0, "Growth": 1, "Momentum": 1, "Low Volatility": -1},
    "SLOWDOWN":   {"Value": -1, "Quality": 1, "Growth": -1, "Momentum": 0, "Low Volatility": 1},
    "STAGFLATION": {"Value": 0, "Quality": 1, "Growth": -1, "Momentum": -1, "Low Volatility": 1},
    "HEATING UP":  {"Value": 1, "Quality": 0, "Growth": 0, "Momentum": 1, "Low Volatility": -1},
}


# ── Input-Defaults und Persistenz-Mapping ──────────────────────────────────
# Die Defaults werden nur verwendet, wenn die Datenbank keinen gespeicherten
# Wert für das jeweilige Feld liefert (Erstbenutzung oder DB nicht erreichbar).
_DEFAULTS: dict[str, float] = {
    "ft-pmi": 48.0,
    "ft-pmi-trend": -3.2,
    "ft-cli": -2.9,
    "ft-spread": 0.5,
    "ft-cpi": 3.3,
    "ft-vix": 31.0,
    "ft-credit": 400.0,
    "ft-pcr": 0.96,
    "ft-flows": -9.9,
    "ft-mom-value": 21.6,
    "ft-mom-quality": 5.7,
    "ft-mom-growth": -1.7,
    "ft-mom-momentum": 10.2,
    "ft-mom-lowvol": 1.0,
}

# Persistence-Feldname ↔ Dash-Input-ID. Die persistierten Felder folgen
# snake_case-Konvention (siehe ``persistence._FACTOR_TIMING_FIELDS``).
_FIELD_TO_INPUT_ID: dict[str, str] = {
    "pmi": "ft-pmi",
    "pmi_trend": "ft-pmi-trend",
    "cli": "ft-cli",
    "spread": "ft-spread",
    "cpi": "ft-cpi",
    "vix": "ft-vix",
    "credit": "ft-credit",
    "pcr": "ft-pcr",
    "flows": "ft-flows",
    "mom_value": "ft-mom-value",
    "mom_quality": "ft-mom-quality",
    "mom_growth": "ft-mom-growth",
    "mom_momentum": "ft-mom-momentum",
    "mom_lowvol": "ft-mom-lowvol",
}
_INPUT_ID_TO_FIELD: dict[str, str] = {v: k for k, v in _FIELD_TO_INPUT_ID.items()}

# Reihenfolge für den Save-Callback (muss zu den Input(...)-Argumenten passen).
_INPUT_ORDER: tuple[str, ...] = (
    "ft-pmi",
    "ft-pmi-trend",
    "ft-cli",
    "ft-spread",
    "ft-cpi",
    "ft-vix",
    "ft-credit",
    "ft-pcr",
    "ft-flows",
    "ft-mom-value",
    "ft-mom-quality",
    "ft-mom-growth",
    "ft-mom-momentum",
    "ft-mom-lowvol",
)


def _strategic_weights() -> dict[str, float]:
    """Liest die strategische Faktor-Allokation aus den App-Einstellungen.

    Mappt die Settings-Keys (``value``, ``quality``, ``growth``, ``momentum``,
    ``lowvol``) auf die im Factor-Timing-System verwendeten Anzeige-Namen
    (``Value``, …, ``Low Volatility``). Normalisiert auf Summe 1, damit ein
    inkonsistenter Settings-State (Summe ≠ 1) hier nicht zu falschen
    Prozentwerten in Diagramm und Tabelle führt. Fallback auf
    ``_STRATEGIC_FALLBACK``, wenn keine Settings vorhanden sind.
    """
    settings = getattr(STATE, "settings", None)
    fw = getattr(settings, "factor_weights", None) if settings is not None else None
    if not isinstance(fw, dict) or not fw:
        return dict(_STRATEGIC_FALLBACK)

    out: dict[str, float] = {}
    for src_key, display in _SETTINGS_TO_STRATEGIC.items():
        try:
            out[display] = float(fw.get(src_key, _STRATEGIC_FALLBACK[display]))
        except (TypeError, ValueError):
            out[display] = _STRATEGIC_FALLBACK[display]
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out


def _resolve_input_values() -> dict[str, float]:
    """Mergt persistierte Eingabewerte mit Defaults — pro Input-ID."""
    resolved = dict(_DEFAULTS)
    stored = load_factor_timing_inputs()
    if not stored:
        return resolved
    for field, value in stored.items():
        input_id = _FIELD_TO_INPUT_ID.get(field)
        if input_id is None or value is None:
            continue
        resolved[input_id] = value
    return resolved


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
    vals = _resolve_input_values()
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
                                        _input("ISM Manufacturing PMI", "ft-pmi", vals["ft-pmi"]),
                                        _input("ISM PMI Trend (MoM)", "ft-pmi-trend", vals["ft-pmi-trend"]),
                                        _input("OECD CLI (YoY %)", "ft-cli", vals["ft-cli"]),
                                        _input("US 10Y-2Y Spread (bps)", "ft-spread", vals["ft-spread"]),
                                        _input("Inflation (CPI YoY %)", "ft-cpi", vals["ft-cpi"]),
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
                                        _input("VIX Index", "ft-vix", vals["ft-vix"]),
                                        _input("Credit Spread (OAS bps)", "ft-credit", vals["ft-credit"]),
                                        _input("Put/Call Ratio", "ft-pcr", vals["ft-pcr"], step=0.01),
                                        _input("Fund Flows (Mrd.)", "ft-flows", vals["ft-flows"]),
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
                                        _input("Value", "ft-mom-value", vals["ft-mom-value"]),
                                        _input("Quality", "ft-mom-quality", vals["ft-mom-quality"]),
                                        _input("Growth", "ft-mom-growth", vals["ft-mom-growth"]),
                                        _input("Momentum", "ft-mom-momentum", vals["ft-mom-momentum"]),
                                        _input("Low Vol", "ft-mom-lowvol", vals["ft-mom-lowvol"]),
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
            dcc.Store(id="ft-save-ack"),
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

    strategic = _strategic_weights()

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
    for factor, strat in strategic.items():
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
        x=list(strategic.keys()),
        y=list(strategic.values()),
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
                            html.Td(fmt_percent(strategic[f], 1)),
                            html.Td(fmt_percent(tactical[f], 1)),
                            html.Td(fmt_signed_percent(tactical[f] - strategic[f], 1)),
                            html.Td(mom_signal[f]),
                        ]
                    )
                    for f in strategic
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


@callback(
    Output("ft-save-ack", "data"),
    [Input(input_id, "value") for input_id in _INPUT_ORDER],
    prevent_initial_call=True,
)
def _persist_inputs(*values):
    """Speichert jede Eingabe-Änderung in der DB.

    Wird bei jedem Wertwechsel ausgelöst (Dash-Input feuert pro Keystroke).
    Bei DB-Fehlern wird geloggt und ``no_update`` zurückgegeben, damit die UI
    nicht unnötig re-rendert. Der Store ``ft-save-ack`` hält keine sichtbare
    Information, dient nur als Dummy-Output für den Callback.
    """
    payload = {
        _INPUT_ID_TO_FIELD[input_id]: value
        for input_id, value in zip(_INPUT_ORDER, values)
    }
    try:
        save_factor_timing_inputs(payload)
    except Exception as exc:  # noqa: BLE001
        log.warning("Factor-Timing-Eingaben konnten nicht gespeichert werden: %s", exc)
        return no_update
    return {"ok": True}


register_page(__name__, path="/factor-timing", name="Factor Timing", layout=layout)
