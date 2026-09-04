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

# Composite-v2-Settings: (Feldname, Label, Schrittweite, int?). Die Inputs
# nutzen Pattern-Matching-IDs {"type": "v2-set", "index": Feldname}, damit
# der Save-Callback nicht jedes Feld einzeln aufzählen muss.
V2_SETTINGS_FIELDS: list[tuple[str, str, float, bool]] = [
    ("v2_weight_value", "Gewicht Value", 0.01, False),
    ("v2_weight_quality", "Gewicht Quality", 0.01, False),
    ("v2_weight_momentum", "Gewicht Momentum", 0.01, False),
    ("v2_weight_investment", "Gewicht Investment", 0.01, False),
    ("v2_min_factor_weight", "Min. Faktorgewichts-Summe", 0.05, False),
    ("v2_min_group_size", "Min. Gruppengröße (Neutralisierung)", 1, True),
    ("v2_min_group_valid", "Min. gültige Werte je Gruppe", 1, True),
    ("v2_winsor_lower", "Winsorisierung unten", 0.01, False),
    ("v2_winsor_upper", "Winsorisierung oben", 0.01, False),
    ("v2_zscore_cap", "Z-Score-Cap (±)", 0.5, False),
    ("v2_composite_winsor_lower", "Composite-Winsor unten", 0.01, False),
    ("v2_composite_winsor_upper", "Composite-Winsor oben", 0.01, False),
    ("v2_min_volatility", "Vola-Floor für mom_12_1_adj", 0.01, False),
]

# Portfoliokonstruktion (alle pc_*- und Filter-Settings der Spec).
PC_SETTINGS_FIELDS: list[tuple[str, str, float, bool]] = [
    ("filter_min_market_cap", "Filter: Min. Market Cap (Mio EUR)", 100, False),
    ("filter_min_piotroski", "Filter: Min. Piotroski (von 9)", 0.5, False),
    ("filter_min_altman", "Filter: Min. Altman Z", 0.1, False),
    ("filter_min_adv", "Filter: Min. ADV 3M (Mio EUR)", 0.5, False),
    ("filter_min_coverage", "Filter: Min. Datenabdeckung v2", 0.05, False),
    ("filter_min_listing_days", "Filter: Min. Tage seit IPO", 5, True),
    ("filter_max_de", "Filter: Max. Debt/Equity", 0.1, False),
    ("filter_min_icr", "Filter: Min. Interest Coverage", 0.1, False),
    ("pc_target_n", "Zielanzahl Titel", 1, True),
    ("pc_min_n", "Mindestanzahl Titel", 1, True),
    ("pc_max_n", "Höchstanzahl Titel", 1, True),
    ("pc_entry_pct", "Einstiegszone (Perzentil)", 0.01, False),
    ("pc_exit_pct", "Ausstiegszone (Perzentil)", 0.001, False),
    ("pc_fill_pct", "Notfüllzone (Perzentil)", 0.01, False),
    ("pc_sector_band", "Sektor-Band (± pp)", 0.01, False),
    ("pc_region_band", "Regions-Band (± pp)", 0.01, False),
    ("pc_max_per_sector", "Max. Titel je Sektor", 1, True),
    ("pc_benchmark_max_age_days", "Max. Alter Benchmark-Gewichte (Tage)", 5, True),
    ("pc_vol_floor", "Vola-Floor (Gewichtung)", 0.01, False),
    ("pc_vol_cap", "Vola-Cap (Gewichtung)", 0.01, False),
    ("pc_weight_floor", "Gewichts-Floor", 0.005, False),
    ("pc_weight_cap", "Gewichts-Cap", 0.005, False),
    ("pc_te_target_low", "TE-Zielband unten", 0.005, False),
    ("pc_te_target_high", "TE-Zielband oben", 0.005, False),
    ("pc_te_max", "TE-Maximum", 0.005, False),
    ("pc_max_cte_share", "Max. CTE-Anteil", 0.01, False),
    ("pc_te_min_coverage", "Min. Kursabdeckung für TE", 0.05, False),
    ("pc_turnover_budget_full", "Turnover-Budget full", 0.01, False),
    ("pc_turnover_budget_interim", "Turnover-Budget interim", 0.01, False),
    ("pc_min_trade_size", "Min. Trade-Größe (|Δw|)", 0.001, False),
]

_V2_INT_FIELDS = {f for f, _, _, is_int in V2_SETTINGS_FIELDS if is_int} | {
    f for f, _, _, is_int in PC_SETTINGS_FIELDS if is_int
}


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


def _numeric_row(id_obj: dict, label: str, value, step, is_int: bool) -> dbc.Row:
    return dbc.Row(
        [
            dbc.Col(html.Label(label), md=7),
            dbc.Col(
                dbc.Input(
                    id=id_obj,
                    type="number",
                    value=value,
                    step=step,
                    min=0 if is_int else None,
                ),
                md=5,
            ),
        ],
        className="mb-2",
    )


def _v2_card() -> dbc.Card:
    """Einstellungs-Block „Composite v2" (Spec 11.2)."""
    s = STATE.settings
    rows = [
        _numeric_row(
            {"type": "v2-set", "index": field}, label, getattr(s, field), step, is_int
        )
        for field, label, step, is_int in V2_SETTINGS_FIELDS
    ]
    minvalid_rows = []
    for segment, table in (("nonfin", s.v2_min_valid_nonfin),
                           ("fin", s.v2_min_valid_financial)):
        seg_label = "Nicht-Financials" if segment == "nonfin" else "Financials"
        for factor in ("value", "quality", "momentum", "investment"):
            minvalid_rows.append(
                _numeric_row(
                    {"type": "v2-minvalid", "index": f"{segment}:{factor}"},
                    f"Min. gültige Indikatoren {seg_label} · {factor}",
                    table.get(factor, 1),
                    1,
                    True,
                )
            )
    return dbc.Card(
        [
            dbc.CardHeader("Composite v2"),
            dbc.CardBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Primäre Score-Version"), md=7),
                            dbc.Col(
                                dbc.Select(
                                    id="v2-scoring-version",
                                    options=[
                                        {"label": "v2 (Composite)", "value": "v2"},
                                        {"label": "v1 (Perzentile)", "value": "v1"},
                                    ],
                                    value=s.scoring_version,
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Faktor-Timing-Modus"), md=7),
                            dbc.Col(
                                dbc.Select(
                                    id="v2-timing-mode",
                                    options=[
                                        {"label": "monitor (empfohlen)",
                                         "value": "monitor"},
                                        {"label": "active (Backtests)",
                                         "value": "active"},
                                    ],
                                    value=s.factor_timing_mode,
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    *rows,
                    html.Div(id="v2-weight-sum", className="ms-weight-sum"),
                    html.Hr(),
                    html.Div("Mindestabdeckungen je Faktor",
                             className="fw-bold small mb-2"),
                    *minvalid_rows,
                ]
            ),
        ],
        className="mb-3",
    )


def _pc_card() -> dbc.Card:
    """Einstellungs-Block „Portfoliokonstruktion" (Spec 11.2)."""
    s = STATE.settings
    rows = [
        _numeric_row(
            {"type": "pc-set", "index": field}, label, getattr(s, field), step, is_int
        )
        for field, label, step, is_int in PC_SETTINGS_FIELDS
    ]
    from app.core.persistence import load_region_weights

    region_weights, region_asof = load_region_weights()
    region_text = "\n".join(
        f"{name}={weight}" for name, weight in sorted(region_weights.items())
    )
    return dbc.Card(
        [
            dbc.CardHeader("Portfoliokonstruktion"),
            dbc.CardBody(
                [
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Benchmark-Quelle (Bänder/Exposures)"), md=7),
                            dbc.Col(
                                dbc.Select(
                                    id="pc-benchmark-source",
                                    options=[
                                        {
                                            "label": "Universum (marktkap.-gew.)",
                                            "value": "universe",
                                        },
                                        {
                                            "label": "Statisch (ACWI, manuell)",
                                            "value": "static",
                                        },
                                    ],
                                    value=s.pc_benchmark_source,
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    *rows,
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Rebalancing-Monate (full)"), md=7),
                            dbc.Col(
                                dbc.Input(
                                    id="pc-rebalance-months",
                                    value=", ".join(
                                        str(m) for m in s.pc_rebalance_months
                                    ),
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("Interim-Monate"), md=7),
                            dbc.Col(
                                dbc.Input(
                                    id="pc-interim-months",
                                    value=", ".join(
                                        str(m) for m in s.pc_interim_months
                                    ),
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(
                                html.Label("Stand ACWI-Sektorgewichte (ISO)"),
                                md=7,
                            ),
                            dbc.Col(
                                dbc.Input(
                                    id="pc-sector-asof",
                                    value=s.risk_benchmark_sector_weights_asof,
                                    placeholder="YYYY-MM-DD",
                                ),
                                md=5,
                            ),
                        ],
                        className="mb-2",
                    ),
                    html.Hr(),
                    html.Div(
                        "Benchmark-Regionsgewichte (eine Zeile je Region: "
                        "Region=Gewicht; Namen exakt wie in der Koyfin-Spalte "
                        "„region“)",
                        className="small mb-1",
                    ),
                    dbc.Textarea(
                        id="pc-region-weights",
                        value=region_text,
                        rows=5,
                        className="mb-2",
                    ),
                    html.Div(
                        f"Stand: {region_asof.isoformat() if region_asof else '–'} "
                        "(Speichern setzt den Stand auf heute)",
                        className="small text-muted",
                    ),
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
                            dbc.Col(html.Label("BUY ab Gesamt-Score"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="buy-threshold",
                                    type="number",
                                    value=s.buy_threshold,
                                    min=0,
                                    max=100,
                                    step=1,
                                ),
                                md=4,
                            ),
                        ],
                        className="mb-2",
                    ),
                    dbc.Row(
                        [
                            dbc.Col(html.Label("SELL unter Gesamt-Score"), md=6),
                            dbc.Col(
                                dbc.Input(
                                    id="sell-threshold",
                                    type="number",
                                    value=s.sell_threshold,
                                    min=0,
                                    max=100,
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
            dbc.Row(
                [
                    dbc.Col(_v2_card(), md=6),
                    dbc.Col(_pc_card(), md=6),
                ],
                className="mb-3",
            ),
            html.Div(
                [
                    dbc.Button(
                        "Composite v2 & Portfoliokonstruktion speichern",
                        id="save-v2-settings",
                        color="dark",
                        size="sm",
                        n_clicks=0,
                    ),
                ],
                className="mb-2",
            ),
            html.Div(id="v2-settings-status", className="mb-3"),
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
    Output("buy-threshold", "value"),
    Output("sell-threshold", "value"),
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
        defaults.buy_threshold,
        defaults.sell_threshold,
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
    State("buy-threshold", "value"),
    State("sell-threshold", "value"),
    State("pct-mode", "value"),
    State({"type": "ind-weight", "index": ALL}, "value"),
    State({"type": "ind-weight", "index": ALL}, "id"),
    prevent_initial_call=True,
)
def _save(
    n_clicks, v, q, g, m, lv, piotr, altman, mcap, minind,
    buy_thr, sell_thr, mode, ind_vals, ind_ids,
):
    if not n_clicks:
        return ""
    s = STATE.settings
    defaults = Settings()
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
    # Leeres Feld → Default; SELL-Schwelle darf die BUY-Schwelle nicht
    # überschreiten, sonst gäbe es kein HOLD-Band mehr.
    s.buy_threshold = float(buy_thr if buy_thr is not None else defaults.buy_threshold)
    s.sell_threshold = min(
        float(sell_thr if sell_thr is not None else defaults.sell_threshold),
        s.buy_threshold,
    )
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


def _parse_months(raw: str | None, fallback: list[int]) -> list[int]:
    if not raw:
        return list(fallback)
    try:
        months = sorted(
            {int(x) for x in str(raw).replace(";", ",").split(",") if x.strip()}
        )
    except ValueError:
        return list(fallback)
    return [m for m in months if 1 <= m <= 12] or list(fallback)


@callback(
    Output("v2-settings-status", "children"),
    Input("save-v2-settings", "n_clicks"),
    State({"type": "v2-set", "index": ALL}, "value"),
    State({"type": "v2-set", "index": ALL}, "id"),
    State({"type": "v2-minvalid", "index": ALL}, "value"),
    State({"type": "v2-minvalid", "index": ALL}, "id"),
    State({"type": "pc-set", "index": ALL}, "value"),
    State({"type": "pc-set", "index": ALL}, "id"),
    State("v2-scoring-version", "value"),
    State("v2-timing-mode", "value"),
    State("pc-benchmark-source", "value"),
    State("pc-rebalance-months", "value"),
    State("pc-interim-months", "value"),
    State("pc-sector-asof", "value"),
    State("pc-region-weights", "value"),
    prevent_initial_call=True,
)
def _save_v2(n_clicks, v2_vals, v2_ids, mv_vals, mv_ids, pc_vals, pc_ids,
             scoring_version, timing_mode, benchmark_source, rebalance_months,
             interim_months, sector_asof, region_weights_text):
    if not n_clicks:
        raise PreventUpdate
    s = STATE.settings
    defaults = Settings()

    for vals, ids in ((v2_vals, v2_ids), (pc_vals, pc_ids)):
        for val, ident in zip(vals or [], ids or [], strict=False):
            field = ident["index"]
            if val is None:
                val = getattr(defaults, field)
            setattr(
                s, field, int(val) if field in _V2_INT_FIELDS else float(val)
            )
    for val, ident in zip(mv_vals or [], mv_ids or [], strict=False):
        segment, factor = ident["index"].split(":", 1)
        table = (
            s.v2_min_valid_nonfin if segment == "nonfin"
            else s.v2_min_valid_financial
        )
        table[factor] = float(int(val)) if val is not None else table.get(factor, 1.0)

    s.scoring_version = scoring_version or "v2"
    s.factor_timing_mode = timing_mode or "monitor"
    s.pc_benchmark_source = benchmark_source or "universe"
    s.pc_rebalance_months = _parse_months(
        rebalance_months, defaults.pc_rebalance_months
    )
    s.pc_interim_months = _parse_months(interim_months, defaults.pc_interim_months)
    s.risk_benchmark_sector_weights_asof = (sector_asof or "").strip()

    # Validierung der v2-Faktorgewichte (Summe 1,0 ± 0,001, Spec 2.4).
    try:
        s.validate_v2_weights()
    except ValueError as exc:
        return dbc.Alert(f"Nicht gespeichert: {exc}", color="danger")

    alerts = []
    # Regionsgewichte in die Tabelle risk_benchmark_region_weights schreiben.
    region_weights: dict[str, float] = {}
    for line in str(region_weights_text or "").splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        name, _, value = line.partition("=")
        try:
            region_weights[name.strip()] = float(value.strip().replace(",", "."))
        except ValueError:
            alerts.append(
                dbc.Alert(
                    f"Regionsgewicht unlesbar, ignoriert: „{line}“",
                    color="warning",
                )
            )
    try:
        from app.core.persistence import save_region_weights

        if region_weights:
            save_region_weights(region_weights, date.today())
    except Exception as exc:  # noqa: BLE001
        alerts.append(
            dbc.Alert(
                f"Warnung: Regionsgewichte nicht gespeichert ({exc}).",
                color="warning",
            )
        )

    STATE.recompute()
    alerts.insert(
        0,
        dbc.Alert(
            "Composite v2 / Portfoliokonstruktion gespeichert. Scores wurden "
            "neu berechnet.",
            color="success",
            duration=4000,
        ),
    )
    try:
        save_settings(s)
    except Exception as exc:  # noqa: BLE001
        alerts.append(
            dbc.Alert(
                f"Warnung: Datenbank-Speicherung fehlgeschlagen ({exc}).",
                color="warning",
            )
        )
    return html.Div(alerts)


@callback(
    Output("v2-weight-sum", "children"),
    Input({"type": "v2-set", "index": ALL}, "value"),
    Input({"type": "v2-set", "index": ALL}, "id"),
)
def _v2_weight_sum(values, ids):
    total = 0.0
    for val, ident in zip(values or [], ids or [], strict=False):
        if ident["index"].startswith("v2_weight_") and val is not None:
            total += float(val)
    note = "" if abs(total - 1.0) <= 0.001 else " — muss 1,0 ± 0,001 ergeben!"
    return f"Summe Faktorgewichte: {fmt_de(total, 2)}{note}"


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
