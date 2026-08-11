"""Einstellungs-Seite (entspricht Sheet ``Einstellungen``)."""

from __future__ import annotations

from datetime import date

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, ctx, html, register_page
from dash.exceptions import PreventUpdate

from app.core import agents_client
from app.core.config import Settings
from app.core.persistence import (
    delete_sector_snapshot,
    list_sector_snapshot_dates,
    save_settings,
)
from app.core.state import STATE
from app.pages.common import page_title
from app.ui import FACTOR_GROUP_LABELS, fmt_de, label_for


FACTOR_FIELDS = [
    ("value", label_for("value")),
    ("quality", label_for("quality")),
    ("growth", label_for("growth")),
    ("momentum", label_for("momentum")),
    ("lowvol", label_for("lowvol")),
]


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


def _snapshot_list() -> html.Div:
    """Rendert die Tabelle der vorhandenen Sektor-Momentum-Snapshots."""

    entries = list_sector_snapshot_dates()
    if not entries:
        return dbc.Alert(
            "Noch keine Snapshots gespeichert.", color="secondary"
        )

    rows = []
    for snap, count in entries:
        rows.append(
            html.Tr(
                [
                    html.Td(snap.strftime("%d.%m.%Y")),
                    html.Td(f"{count} Ticker"),
                    html.Td(
                        dbc.Button(
                            "Löschen",
                            id={"type": "sm-delete", "index": snap.isoformat()},
                            color="danger",
                            outline=True,
                            size="sm",
                        ),
                        className="text-end",
                    ),
                ]
            )
        )

    header = html.Thead(
        html.Tr(
            [
                html.Th("Datum"),
                html.Th("Inhalt"),
                html.Th("Aktion", className="text-end"),
            ]
        )
    )
    return dbc.Table(
        [header, html.Tbody(rows)],
        bordered=False,
        hover=True,
        responsive=True,
        size="sm",
        className="mb-0",
    )


def _snapshot_card() -> dbc.Card:
    return dbc.Card(
        [
            dbc.CardHeader("Sektor-Momentum-Snapshots"),
            dbc.CardBody(
                [
                    html.P(
                        "Falsches Datum gewählt? Hier den fehlerhaften "
                        "Snapshot löschen und auf der Sektor-Momentum-Seite "
                        "mit dem korrekten Datum erneut hochladen.",
                        className="text-muted small mb-3",
                    ),
                    html.Div(id="sm-snapshot-list", children=_snapshot_list()),
                    html.Div(id="sm-snapshot-status", className="mt-2"),
                ]
            ),
        ],
        className="mb-3",
    )


def _indicator_table(title: str, weights: dict[str, float], prefix: str) -> dbc.Card:
    rows = []
    for key, val in weights.items():
        rows.append(
            dbc.Row(
                [
                    dbc.Col(
                        html.Label(
                            label_for(key),
                            htmlFor=f"ind-weight-{prefix}-{key}",
                        ),
                        md=7,
                    ),
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
                        md=5,
                    ),
                ],
                className="mb-1 align-items-center",
            )
        )
    return dbc.Card(
        [dbc.CardHeader(title), dbc.CardBody(rows)],
        className="mb-3",
    )


# Sprachen der Agenten-Reports: Wire-Werte des TradingAgents-Service
# (Freitext-Feld ``output_language``), Auswahl gespiegelt aus dessen CLI.
AGENT_LANGUAGES: tuple[tuple[str, str], ...] = (
    ("Deutsch", "German"),
    ("Englisch", "English"),
    ("Französisch", "French"),
    ("Spanisch", "Spanish"),
    ("Portugiesisch", "Portuguese"),
    ("Chinesisch", "Chinese"),
    ("Japanisch", "Japanese"),
    ("Koreanisch", "Korean"),
    ("Hindi", "Hindi"),
    ("Arabisch", "Arabic"),
    ("Russisch", "Russian"),
)


def _agents_card() -> dbc.Card:
    """Karte „Agenten-Tiefenanalyse“: Service-Status + Provider/Modelle/Tiefe."""
    import os

    s = STATE.settings
    url = os.getenv("TRADINGAGENTS_URL", "http://localhost:8000")
    catalog = agents_client.get_catalog()

    if catalog is None:
        status = html.Span("nicht erreichbar", className="text-danger")
        provider_options = []
    else:
        status = html.Span("verbunden", className="text-success")
        provider_options = [
            {"label": p.get("label") or p.get("key"), "value": p.get("key")}
            for p in (catalog.get("providers") or [])
            if p.get("key")
        ]

    depth_options = (
        [
            {"label": d.get("label"), "value": d.get("value")}
            for d in (catalog.get("depths") or [])
        ]
        if catalog
        else [
            {"label": "Shallow (1)", "value": 1},
            {"label": "Medium (3)", "value": 3},
            {"label": "Deep (5)", "value": 5},
        ]
    )

    def _model_row(label: str, input_id: str, value: str) -> dbc.Row:
        return dbc.Row(
            [
                dbc.Col(html.Label(label), md=5),
                dbc.Col(
                    dbc.Input(id=input_id, type="text", value=value,
                              placeholder="Service-Default"),
                    md=7,
                ),
            ],
            className="mb-2",
        )

    return dbc.Card(
        [
            dbc.CardHeader("Agenten-Tiefenanalyse (TradingAgents)"),
            dbc.CardBody(
                [
                    html.Div(
                        [
                            html.Span("Service: ", className="fw-bold"),
                            html.Code(url),
                            html.Span(" — "),
                            status,
                            html.Span(
                                " (Konfiguration über Umgebungsvariable "
                                "TRADINGAGENTS_URL)",
                                className="ms-tt-muted small",
                            ),
                        ],
                        className="mb-3",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("LLM-Provider"), md=5),
                            dbc.Col(
                                dbc.Select(
                                    id="agents-provider",
                                    options=provider_options,
                                    value=s.agents_provider or None,
                                    placeholder="Service-Default",
                                )
                                if provider_options
                                else dbc.Input(
                                    id="agents-provider",
                                    type="text",
                                    value=s.agents_provider,
                                    placeholder="z. B. anthropic (Service down — Freitext)",
                                ),
                                md=7,
                            ),
                        ],
                        className="mb-2",
                    ),
                    _model_row(
                        "Quick-Modell (Analysten)",
                        "agents-quick-model",
                        s.agents_quick_model,
                    ),
                    _model_row(
                        "Deep-Modell (Manager)",
                        "agents-deep-model",
                        s.agents_deep_model,
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Analysetiefe"), md=5),
                            dbc.Col(
                                dbc.Select(
                                    id="agents-depth",
                                    options=depth_options,
                                    value=s.agents_depth,
                                ),
                                md=7,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Temperatur (0 = deterministisch)"), md=5),
                            dbc.Col(
                                dbc.Input(
                                    id="agents-temperature",
                                    type="number",
                                    value=s.agents_temperature,
                                    min=0,
                                    max=2,
                                    step=0.1,
                                ),
                                md=7,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                dbc.Checkbox(
                                    id="agents-prev-analysis",
                                    value=s.agents_prev_analysis,
                                    label=(
                                        "Vergleich mit letzter Analyse (Kontext für "
                                        "Research/Portfolio Manager)"
                                    ),
                                ),
                                md=12,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Sprache der Analyse"), md=5),
                            dbc.Col(
                                dbc.Select(
                                    id="agents-language",
                                    options=[
                                        {"label": label, "value": value}
                                        for label, value in AGENT_LANGUAGES
                                    ]
                                    + (
                                        # Frei gespeicherte Sprache (z. B. via DB)
                                        # bleibt wählbar, auch wenn nicht gelistet.
                                        [{"label": s.agents_language,
                                          "value": s.agents_language}]
                                        if s.agents_language
                                        and s.agents_language
                                        not in {v for _, v in AGENT_LANGUAGES}
                                        else []
                                    ),
                                    value=s.agents_language or "German",
                                ),
                                md=7,
                            ),
                        ],
                        className="mb-2",
                    ),
                    html.Div(
                        "Leere Felder verwenden die Defaults des Service "
                        "(Katalog/Umgebung). API-Keys werden im "
                        "TradingAgents-Service konfiguriert, nicht hier.",
                        className="ms-tt-muted small mb-2",
                    ),
                    dbc.Button(
                        "Agenten-Einstellungen speichern",
                        id="save-agents-settings",
                        color="dark",
                        size="sm",
                    ),
                    html.Div(id="agents-settings-status", className="mt-2 small"),
                ]
            ),
        ],
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

    factor_sum = html.Div(
        id="factor-sum",
        className="ms-weight-sum",
    )

    indicator_cards = dbc.Row(
        [
            dbc.Col(_indicator_table(FACTOR_GROUP_LABELS["value"], s.value_weights, "value"), md=6),
            dbc.Col(_indicator_table(FACTOR_GROUP_LABELS["quality"], s.quality_weights, "quality"), md=6),
            dbc.Col(_indicator_table(FACTOR_GROUP_LABELS["growth"], s.growth_weights, "growth"), md=6),
            dbc.Col(_indicator_table(FACTOR_GROUP_LABELS["momentum"], s.momentum_weights, "momentum"), md=6),
            dbc.Col(_indicator_table(FACTOR_GROUP_LABELS["lowvol"], s.lowvol_weights, "lowvol"), md=6),
        ]
    )

    return html.Div(
        [
            page_title(
                "Einstellungen",
                "Faktor- und Indikator-Gewichtungen sowie Filter-Schwellen.",
            ),
            dbc.Row(
                [
                    dbc.Col(
                        dbc.Card(
                            [
                                dbc.CardHeader("Faktor-Gewichtungen"),
                                dbc.CardBody([*factor_rows, factor_sum]),
                            ]
                        ),
                        md=6,
                    ),
                    dbc.Col(filters, md=6),
                ],
                className="mb-3",
            ),
            indicator_cards,
            _agents_card(),
            _snapshot_card(),
            html.Div(
                [
                    dbc.Button(
                        "Speichern & neu berechnen",
                        id="save-settings",
                        color="primary",
                        size="lg",
                    ),
                    dbc.Button(
                        "Auf Standard zurücksetzen",
                        id="reset-settings",
                        color="secondary",
                        outline=True,
                        className="ms-2",
                    ),
                ],
                className="mt-3 d-flex gap-2",
            ),
            html.Div(id="settings-status", className="mt-3"),
        ]
    )


@callback(
    Output("factor-sum", "children"),
    Output("factor-sum", "className"),
    Input({"type": "factor-weight", "index": ALL}, "value"),
)
def _factor_sum(values: list) -> tuple[list, str]:
    total = sum(float(v or 0) for v in values)
    ok = abs(total - 1.0) < 1e-6
    label = [
        html.Span("Summe", className="ms-weight-sum-label"),
        html.Span(fmt_de(total, 2), className="ms-weight-sum-value"),
    ]
    if not ok:
        label.append(
            html.Span(
                "wird beim Speichern normalisiert" if total > 0 else "Summe muss > 0 sein",
                className="ms-weight-sum-hint",
            )
        )
    cls = "ms-weight-sum " + ("is-ok" if ok else "is-warn")
    return label, cls


@callback(
    Output({"type": "factor-weight", "index": ALL}, "value"),
    Output({"type": "ind-weight", "index": ALL}, "value"),
    Output("min-piotroski", "value"),
    Output("min-altman", "value"),
    Output("min-mcap", "value"),
    Output("min-industry", "value"),
    Output("pct-mode", "value"),
    Input("reset-settings", "n_clicks"),
    State({"type": "factor-weight", "index": ALL}, "id"),
    State({"type": "ind-weight", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def _reset(_n, factor_ids, ind_ids):
    defaults = Settings()
    factor_vals = [defaults.factor_weights[ident["index"]] for ident in factor_ids]
    groups = {
        "value": defaults.value_weights,
        "quality": defaults.quality_weights,
        "growth": defaults.growth_weights,
        "momentum": defaults.momentum_weights,
        "lowvol": defaults.lowvol_weights,
    }
    ind_vals = []
    for ident in ind_ids:
        prefix, key = ident["index"].split(":", 1)
        ind_vals.append(groups.get(prefix, {}).get(key, 0))
    return (
        factor_vals,
        ind_vals,
        defaults.min_piotroski,
        defaults.min_altman_z,
        defaults.min_market_cap,
        defaults.min_stocks_per_industry,
        defaults.percentile_mode,
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

    alerts = [
        dbc.Alert(
            "Einstellungen gespeichert. Scores wurden neu berechnet.",
            color="success",
            # Automatisch ausblenden: eine dauerhaft stehende Bestätigung
            # wirkt beim zweiten Speichern wie ein veralteter Status.
            duration=4000,
        )
    ]
    try:
        save_settings(s)
    except Exception as exc:  # noqa: BLE001
        alerts.append(
            dbc.Alert(
                f"Warnung: Datenbank-Speicherung fehlgeschlagen ({exc}). "
                "Änderungen sind nur bis zum Neustart der App verfügbar.",
                color="warning",
            )
        )
    return html.Div(alerts)


@callback(
    Output("sm-snapshot-list", "children"),
    Output("sm-snapshot-status", "children"),
    Input({"type": "sm-delete", "index": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _delete_snapshot(n_clicks_list):
    if not any(n_clicks_list or []):
        raise PreventUpdate
    triggered = ctx.triggered_id
    if not isinstance(triggered, dict):
        raise PreventUpdate

    try:
        snap = date.fromisoformat(triggered["index"])
    except (KeyError, ValueError):
        raise PreventUpdate

    try:
        n = delete_sector_snapshot(snap)
        alert = dbc.Alert(
            f"✓ Snapshot vom {snap:%d.%m.%Y} gelöscht "
            f"({fmt_de(n, 0)} Ticker).",
            color="success",
        )
    except Exception as exc:  # noqa: BLE001
        alert = dbc.Alert(
            f"Warnung: Löschen fehlgeschlagen ({exc}).",
            color="warning",
        )

    return _snapshot_list(), alert


@callback(
    Output("agents-settings-status", "children"),
    Input("save-agents-settings", "n_clicks"),
    State("agents-provider", "value"),
    State("agents-quick-model", "value"),
    State("agents-deep-model", "value"),
    State("agents-depth", "value"),
    State("agents-language", "value"),
    State("agents-temperature", "value"),
    State("agents-prev-analysis", "value"),
    prevent_initial_call=True,
)
def _save_agents(n_clicks, provider, quick, deep, depth, language, temperature,
                 prev_analysis):
    if not n_clicks:
        raise PreventUpdate
    s = STATE.settings
    s.agents_provider = (provider or "").strip()
    s.agents_quick_model = (quick or "").strip()
    s.agents_deep_model = (deep or "").strip()
    s.agents_language = (language or "").strip() or "German"
    s.agents_prev_analysis = bool(prev_analysis)
    try:
        s.agents_depth = int(depth) if depth else 1
    except (TypeError, ValueError):
        s.agents_depth = 1
    try:
        s.agents_temperature = max(0.0, min(2.0, float(temperature)))
    except (TypeError, ValueError):
        s.agents_temperature = 0.0
    try:
        save_settings(s)
    except Exception as exc:  # noqa: BLE001 — DB down: Einstellungen nur im Speicher
        return html.Span(
            f"Gespeichert (nur für diese Sitzung — DB nicht erreichbar: {exc})",
            className="text-warning",
        )
    return dbc.Alert(
        "Agenten-Einstellungen gespeichert.",
        color="success",
        duration=4000,
        className="mb-0 py-2",
    )


register_page(__name__, path="/einstellungen", name="Einstellungen", layout=layout)
