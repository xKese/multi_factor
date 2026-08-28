"""Factor-Timing-Modul (entspricht Sheet ``Factor_Timing``).

Regelbasierte taktische Faktor-Allokation. Die Regeln selbst leben in
``app/core/factor_timing`` (Regime v2 mit Hysterese und Zinskurve,
symmetrische Sentiment-Tilts, Tilt-Zerlegung) — diese Seite ist nur noch
UI: Eingaben, AV-Makro-Fetch, Universums-Momentum-Vorbelegung, Anzeige.
"""

from __future__ import annotations

import logging
from datetime import date

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html, no_update, register_page
from dash.exceptions import PreventUpdate

from app.core import av_client
from app.core import factor_timing as ft_core
from app.core.persistence import (
    load_factor_timing_history,
    load_factor_timing_inputs,
    save_factor_timing_inputs,
    save_factor_timing_snapshot,
)
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import MS_LIGHT, fmt_de, fmt_percent, fmt_signed_percent, ms_badge, section_header


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
    "ft-mom-value",
    "ft-mom-quality",
    "ft-mom-growth",
    "ft-mom-momentum",
    "ft-mom-lowvol",
)

# Faktor → Momentum-Input-ID (für die Universums-Vorbelegung).
_FACTOR_TO_MOM_INPUT: dict[str, str] = {
    "Value": "ft-mom-value",
    "Quality": "ft-mom-quality",
    "Growth": "ft-mom-growth",
    "Momentum": "ft-mom-momentum",
    "Low Volatility": "ft-mom-lowvol",
}


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


def _universe_momentum_hint() -> str:
    """Kurztext der aus dem Universum berechneten Momentum-Proxys."""
    vals = ft_core.factor_momentum_from_universe(STATE.scored)
    if not vals:
        return "Kein Universum geladen — Momentum manuell pflegen."
    parts = [f"{f}: {fmt_de(v, 1)} pp" for f, v in vals.items()]
    return "Aus Universum (Top−Bottom-Quintil, 6M): " + " · ".join(parts)


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
                                        _input("US 10Y-2Y Spread (%-Pkt.)", "ft-spread", vals["ft-spread"]),
                                        _input("Inflation (CPI YoY %)", "ft-cpi", vals["ft-cpi"]),
                                        dbc.Button(
                                            "Spread & CPI via Alpha Vantage laden",
                                            id="ft-av-fetch",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            className="mt-1",
                                        ),
                                        html.Div(
                                            id="ft-av-status",
                                            className="ms-tt-muted small mt-2",
                                        ),
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
                                        html.Div(
                                            "Alle drei Signale fließen in die "
                                            "Sentiment-Tilts ein (siehe Zerlegung "
                                            "unten).",
                                            className="ms-tt-muted small mt-2",
                                        ),
                                    ]
                                ),
                            ]
                        ),
                        md=4,
                    ),
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Factor Momentum 6M (pp)"),
                                dbc.CardBody(
                                    [
                                        _input("Value", "ft-mom-value", vals["ft-mom-value"]),
                                        _input("Quality", "ft-mom-quality", vals["ft-mom-quality"]),
                                        _input("Growth", "ft-mom-growth", vals["ft-mom-growth"]),
                                        _input("Momentum", "ft-mom-momentum", vals["ft-mom-momentum"]),
                                        _input("Low Vol", "ft-mom-lowvol", vals["ft-mom-lowvol"]),
                                        dbc.Button(
                                            "Aus Universum übernehmen",
                                            id="ft-mom-auto",
                                            color="secondary",
                                            outline=True,
                                            size="sm",
                                            className="mt-1",
                                        ),
                                        html.Div(
                                            _universe_momentum_hint(),
                                            id="ft-mom-hint",
                                            className="ms-tt-muted small mt-2",
                                        ),
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


def _regime_timeline() -> html.Div | None:
    """Kleine Timeline der zuletzt persistierten Regime-Entscheidungen."""
    history = load_factor_timing_history(limit=10)
    if not history:
        return None
    parts = [
        f"{h['snapshot_date']:%d.%m.} {h['regime']}"
        for h in history
    ]
    return html.Div(
        "Regime-Verlauf: " + " · ".join(parts),
        className="ms-tt-muted small mt-2",
    )


def _fmt_tilt(value: float) -> str:
    """Tilt in Prozentpunkten mit Vorzeichen (0 → '–')."""
    if abs(value) < 1e-9:
        return "–"
    sign = "+" if value > 0 else "−"
    return f"{sign}{fmt_de(abs(value) * 100, 1)} pp"


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
    Input("ft-mom-value", "value"),
    Input("ft-mom-quality", "value"),
    Input("ft-mom-growth", "value"),
    Input("ft-mom-momentum", "value"),
    Input("ft-mom-lowvol", "value"),
)
def _compute(pmi, pmi_trend, cli, spread, cpi, vix, credit, pcr,
             v, q, g, m, lv):
    pmi = float(pmi or 0)
    pmi_trend = float(pmi_trend or 0)
    cli = float(cli or 0)
    spread_val = None if spread is None else float(spread)
    cpi = float(cpi or 0)

    # Vorheriges Regime für die PMI-Hysterese aus der persistierten Timeline.
    history = load_factor_timing_history(limit=1)
    prev_regime = history[0]["regime"] if history else None

    regime = ft_core.detect_regime(
        pmi, pmi_trend, cli, cpi, spread=spread_val, prev_regime=prev_regime
    )

    strategic = _strategic_weights()

    momentum = {
        "Value": float(v or 0),
        "Quality": float(q or 0),
        "Growth": float(g or 0),
        "Momentum": float(m or 0),
        "Low Volatility": float(lv or 0),
    }
    mom_signal = ft_core.momentum_signal(momentum)
    sent_tilts, fired_rules = ft_core.sentiment_tilts(
        float(vix or 0), float(credit or 0), None if pcr is None else float(pcr)
    )

    decomp = ft_core.tactical_weights(strategic, regime, mom_signal, sent_tilts)
    tactical = {f: d["tactical"] for f, d in decomp.items()}

    # Tages-Snapshot für die Regime-Timeline (fail-open: UI läuft auch ohne DB).
    try:
        save_factor_timing_snapshot(date.today(), regime, tactical)
    except Exception as exc:  # noqa: BLE001
        log.warning("Factor-Timing-Snapshot nicht gespeichert: %s", exc)

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
        ft_core.REGIME_GOLDILOCKS: "up",
        ft_core.REGIME_HEATING_UP: "warn",
        ft_core.REGIME_SLOWDOWN: "down",
        ft_core.REGIME_STAGFLATION: "down",
    }.get(regime)
    badges = [ms_badge("Aktuelles Regime", regime, tone=regime_tone)]

    # Value-Spread als reiner Anzeige-Hinweis (kein Tilt — ohne Historie
    # kein z-Score möglich).
    vs = ft_core.value_spread(STATE.scored)
    if vs is not None:
        badges.append(
            ms_badge(
                "Value-Spread",
                f"Top-Value-P/E = {fmt_de(vs * 100, 0)} % des Universums-Medians",
                tone="up" if vs < 0.6 else None,
            )
        )
    badge_row = html.Div(badges, className="ms-badge-row")

    table = dbc.Table(
        [
            html.Thead(
                html.Tr(
                    [
                        html.Th(x)
                        for x in [
                            "Faktor",
                            "Strategisch",
                            "Regime",
                            "Momentum",
                            "Sentiment",
                            "Taktisch",
                            "Δ",
                            "Momentum-Signal",
                        ]
                    ]
                )
            ),
            html.Tbody(
                [
                    html.Tr(
                        [
                            html.Td(f),
                            html.Td(fmt_percent(d["strategic"], 1)),
                            html.Td(_fmt_tilt(d["regime_tilt"])),
                            html.Td(_fmt_tilt(d["momentum_tilt"])),
                            html.Td(_fmt_tilt(d["sentiment_tilt"])),
                            html.Td(fmt_percent(d["tactical"], 1)),
                            html.Td(fmt_signed_percent(d["tactical"] - d["strategic"], 1)),
                            html.Td(mom_signal[f]),
                        ]
                    )
                    for f, d in decomp.items()
                ]
            ),
        ],
        bordered=True,
        striped=True,
        hover=True,
    )

    rules_line = html.Div(
        "Aktive Sentiment-Regeln: "
        + (" · ".join(fired_rules) if fired_rules else "keine"),
        className="ms-tt-muted small mt-1",
    )
    children = [
        badge_row,
        section_header("Allokation", subtitle=f"Regime: {regime}"),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
        table,
        rules_line,
    ]
    timeline = _regime_timeline()
    if timeline is not None:
        children.append(timeline)
    return html.Div(children)


@callback(
    Output("ft-spread", "value"),
    Output("ft-cpi", "value"),
    Output("ft-av-status", "children"),
    Input("ft-av-fetch", "n_clicks"),
    prevent_initial_call=True,
)
def _fetch_macro(n_clicks):
    """Zinskurve (10Y−2Y) und CPI-YoY aus der Alpha-Vantage-API übernehmen.

    PMI/CLI/VIX bleiben manuelle Eingaben — die AV-API führt sie nicht.
    Die gefüllten Inputs werden über den normalen Persist-Callback
    gespeichert."""
    if not n_clicks:
        raise PreventUpdate
    rpm = int(getattr(STATE.settings, "risk_av_requests_per_minute", 70) or 70)
    try:
        y10 = av_client.fetch_treasury_yield("10year", rpm)
        y2 = av_client.fetch_treasury_yield("2year", rpm)
        cpi_series = av_client.fetch_cpi(rpm)
    except av_client.AlphaVantageError as exc:
        return no_update, no_update, f"⚠ {exc}"
    except Exception as exc:  # noqa: BLE001 — Netzwerkfehler etc.
        return no_update, no_update, f"⚠ Abruf fehlgeschlagen: {exc}"

    spread = ft_core.spread_from_yields(y10, y2)
    cpi = ft_core.cpi_yoy(cpi_series)
    if spread is None and cpi is None:
        return no_update, no_update, "⚠ Keine verwertbaren Makro-Daten erhalten."
    parts = []
    if spread is not None:
        parts.append(f"Spread 10Y−2Y: {fmt_de(spread, 2)} %-Pkt.")
    if cpi is not None:
        parts.append(f"CPI YoY: {fmt_de(cpi, 1)} %")
    status = "✓ Übernommen — " + " · ".join(parts)
    return (
        no_update if spread is None else spread,
        no_update if cpi is None else cpi,
        status,
    )


@callback(
    Output("ft-mom-value", "value"),
    Output("ft-mom-quality", "value"),
    Output("ft-mom-growth", "value"),
    Output("ft-mom-momentum", "value"),
    Output("ft-mom-lowvol", "value"),
    Output("ft-mom-hint", "children"),
    Input("ft-mom-auto", "n_clicks"),
    prevent_initial_call=True,
)
def _fill_momentum_from_universe(n_clicks):
    """Momentum-Felder mit dem Universums-Proxy vorbelegen (überschreibbar)."""
    if not n_clicks:
        raise PreventUpdate
    vals = ft_core.factor_momentum_from_universe(STATE.scored)
    if not vals:
        return (
            no_update,
            no_update,
            no_update,
            no_update,
            no_update,
            "⚠ Kein Universum geladen (oder zu wenige Titel) — bitte zuerst "
            "einen Koyfin-Export importieren.",
        )
    out = [
        vals.get(factor, no_update)
        for factor in ("Value", "Quality", "Growth", "Momentum", "Low Volatility")
    ]
    return (*out, _universe_momentum_hint())


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
