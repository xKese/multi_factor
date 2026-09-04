"""Dash-Seite „Risiko & Benchmark" — Ansicht der Risiko-Analytik.

Rendert strikt aus dem lokalen Kurscache (nie API-Calls aus Callbacks):
Der Cache wird ausschließlich über die CLI gefüllt
(``python -m app.tools.risk_report update``), damit Seitenaufrufe weder
Rate-Limits ziehen noch auf die Alpha-Vantage-API warten. Fehlt der Cache,
zeigt die Seite den CLI-Hinweis statt eines Fehlers.
"""

from __future__ import annotations

import logging
from datetime import date

import pandas as pd
import plotly.graph_objects as go
from dash import dcc, html, register_page
from dash.dash_table.Format import Format, Scheme, Symbol

from app.core.risk_report import compute_risk_report
from app.core.state import STATE
from app.pages.common import page_title, render_basic_table, render_table
from app.ui import fmt_de, fmt_percent, fmt_signed_percent, kpi_band, section_header
from app.ui.formatters import fmt_int
from app.ui.theme import LIGHT

# Plotly kann keine CSS-Variablen auflösen → konkrete Palette-Werte.
_COLOR_UP = LIGHT["up"]
_COLOR_DOWN = LIGHT["down"]

log = logging.getLogger(__name__)

_UPDATE_CMD = "python -m app.tools.risk_report update"


def _hint_card(text: str) -> html.Div:
    return html.Div(
        [
            html.Div("Keine Daten", className="ms-kpi-label"),
            html.P(text, className="mb-2"),
            html.Code(_UPDATE_CMD),
        ],
        className="ms-card p-4",
        style={
            "border": "1px solid var(--ms-border)",
            "borderRadius": "4px",
            "background": "var(--ms-surface)",
        },
    )


def _kpis(res: dict) -> html.Div:
    expost = res["expost"]
    mcte = res["mcte"]
    ir = expost.get("information_ratio")
    aktiv_pa = expost.get("aktive_rendite_pa")
    cells = [
        {
            "label": "TE ex-ante (Ledoit-Wolf)",
            "value": fmt_percent(mcte.te_ledoit_wolf) if mcte else "-",
            "sub": f"Sample: {fmt_percent(mcte.te_sample)}" if mcte else res["mcte_fehler"],
        },
        {
            "label": "TE ex-post (1 Jahr)",
            "value": fmt_percent(expost.get("te_1j")),
            "sub": f"Gesamt: {fmt_percent(expost.get('te_gesamt'))}",
        },
        {
            "label": "Aktive Rendite p. a.",
            "value": fmt_signed_percent(aktiv_pa),
            "tone": "up" if (aktiv_pa or 0) > 0 else "down",
            "sub": f"IR: {fmt_de(ir)}",
        },
        {
            "label": "Aktives Beta",
            "value": fmt_de(expost.get("aktives_beta")),
            "sub": f"Korrelation: {fmt_de(expost.get('korrelation'))}",
        },
        {
            "label": "Max. rel. Drawdown",
            "value": fmt_percent(expost.get("max_rel_drawdown")),
            "tone": "down",
            "sub": f"{fmt_int(expost.get('n_tage'))} Handelstage",
        },
    ]
    return kpi_band(cells)


def _te_chart(res: dict) -> dcc.Graph:
    fig = go.Figure()
    for label, series in res["rolling_te"].items():
        if not len(series):
            continue
        fig.add_trace(
            go.Scatter(
                x=series.index,
                y=series.values,
                mode="lines",
                name=f"TE rollierend {label}",
            )
        )
    fig.update_layout(
        margin={"l": 40, "r": 16, "t": 8, "b": 32},
        height=320,
        legend={"orientation": "h", "y": 1.1},
        yaxis={"tickformat": ".1%", "title": None},
        xaxis={"title": None},
    )
    return dcc.Graph(figure=fig, config={"displayModeBar": False})


_MCTE_COLUMNS = [
    {"name": "Titel", "id": "ticker", "presentation": "markdown"},
    {
        "name": "Gewicht",
        "id": "gewicht",
        "type": "numeric",
        "format": Format(precision=1, scheme=Scheme.percentage_rounded, nully="-"),
    },
    {
        "name": "CTE",
        "id": "cte_bp",
        "type": "numeric",
        "format": Format(precision=0, scheme=Scheme.fixed, nully="-")
        .symbol(Symbol.yes)
        .symbol_suffix(" bp"),
    },
    {
        "name": "MCTE",
        "id": "mcte",
        "type": "numeric",
        "format": Format(precision=1, scheme=Scheme.percentage_rounded, nully="-"),
    },
    {
        "name": "Score",
        "id": "total_score",
        "type": "numeric",
        "format": Format(precision=1, scheme=Scheme.fixed, nully="-"),
    },
    {"name": "Empfehlung", "id": "recommendation"},
    {"name": "SMA-Signal", "id": "sma_signal"},
]


def _mcte_section(res: dict) -> list:
    children: list = [
        section_header(
            "Risikobeiträge je Einzeltitel (MCTE)",
            "Aktive Renditen, 2 Jahre, Ledoit-Wolf-Kovarianz — Σ CTE = TE.",
        )
    ]
    if res["mcte"] is None:
        children.append(html.P(res["mcte_fehler"]))
        return children
    mcte = res["mcte"]
    if mcte.ausgeschlossen:
        children.append(
            html.P(
                f"⚠ Ohne ausreichende Historie: {', '.join(mcte.ausgeschlossen)} "
                f"({fmt_percent(mcte.ausgeschlossen_gewicht)} Gewicht) — "
                "übrige Gewichte renormalisiert.",
                className="ms-page-subtitle",
            )
        )
    ranking = res["ranking"][
        [c["id"] for c in _MCTE_COLUMNS if c["id"] in res["ranking"].columns]
    ]
    children.append(
        render_table(ranking, columns=_MCTE_COLUMNS, id="risk-mcte-table")
    )
    if not res["sektor_cte"].empty:
        sektor = res["sektor_cte"].copy()
        sektor["gewicht"] = sektor["gewicht"].map(fmt_percent)
        sektor["cte_bp"] = sektor["cte_bp"].map(
            lambda v: f"{fmt_int(round(v))} bp"
        )
        sektor = sektor.drop(columns=["cte"]).rename(
            columns={"sektor": "Sektor", "gewicht": "Gewicht", "cte_bp": "CTE"}
        )
        children += [
            section_header("CTE je GICS-Sektor"),
            render_basic_table(sektor),
        ]
    return children


def _sector_chart(res: dict) -> list:
    df = res["sektor_allokation"]
    if df.empty:
        return []
    fig = go.Figure(
        go.Bar(
            x=df["aktiv"] * 100.0,
            y=df["sektor"],
            orientation="h",
            marker_color=[
                _COLOR_UP if v >= 0 else _COLOR_DOWN for v in df["aktiv"]
            ],
        )
    )
    fig.update_layout(
        margin={"l": 8, "r": 16, "t": 8, "b": 32},
        height=max(240, 28 * len(df)),
        xaxis={"title": "Aktives Gewicht (Prozentpunkte)"},
        yaxis={"autorange": "reversed", "title": None},
    )
    subtitle = (
        "Portfolio vs. Universum (marktkapitalisierungsgewichtete Anteile "
        "des Daten-Imports)."
        if res.get("sektor_benchmark_quelle") == "universe"
        else "Portfolio vs. MSCI ACWI (statische Gewichte, quartalsweise "
        "gepflegt)."
    )
    return [
        section_header("Aktive Sektorallokation", subtitle),
        dcc.Graph(figure=fig, config={"displayModeBar": False}),
    ]


def _scenario_section(res: dict) -> list:
    if not res["szenarien"]:
        return []
    rows = []
    for s in res["szenarien"]:
        rows.append(
            {
                "Szenario": s.name + ("" if s.belastbar else " ⚠ nicht belastbar"),
                "Fenster": f"{s.start:%d.%m.%Y} – {s.ende:%d.%m.%Y}",
                "Abdeckung": fmt_percent(s.coverage),
                "Portfolio": fmt_signed_percent(s.pf_rendite),
                "Benchmark": fmt_signed_percent(s.bm_rendite),
                "Aktiv": fmt_signed_percent(s.aktiv),
                "Max. Drawdown": fmt_percent(s.max_drawdown),
            }
        )
    return [
        section_header(
            "Historische Szenarien",
            "Replay der heutigen Gewichte; Gewichte auf verfügbare Titel "
            "renormalisiert, Abdeckung < 60 % → nicht belastbar.",
        ),
        render_basic_table(pd.DataFrame(rows)),
    ]


def _shock_section(res: dict) -> list:
    children: list = [
        section_header(
            "Hypothetische Faktor-Schocks",
            "Wochenrenditen (3 Jahre) auf Markt, Δ10Y (bp), WTI, EURUSD "
            "regressiert; Schocks über Betas propagiert.",
        )
    ]
    if res["schock_fehler"]:
        children.append(html.P(res["schock_fehler"]))
        return children
    if res["schocks"].empty:
        children.append(html.P("Keine Schock-Szenarien konfiguriert."))
        return children
    schocks = res["schocks"].copy()
    schocks = pd.DataFrame(
        {
            "Szenario": schocks["szenario"],
            "Portfolio-P&L": schocks["pf_pnl"].map(fmt_signed_percent),
            "Benchmark-P&L": schocks["bm_pnl"].map(fmt_signed_percent),
            "Aktiver Effekt": schocks["aktiv"].map(fmt_signed_percent),
            "Abdeckung": schocks["abdeckung"].map(fmt_percent),
        }
    )
    children.append(render_basic_table(schocks))
    betas = res["betas"]
    if not betas.empty and betas["geringe_guete"].any():
        low = betas[betas["geringe_guete"]]
        children.append(
            html.P(
                "⚠ Geringe Erklärungsgüte (R² < 0,2 oder kurze Historie): "
                + ", ".join(
                    f"{r['ticker']} (R² {fmt_de(r['r2'])})"
                    for _, r in low.iterrows()
                ),
                className="ms-page-subtitle mt-2",
            )
        )
    return children


def _quality_section(res: dict) -> list:
    quality = res["quality"]
    items: list = []
    if quality.unresolved:
        items.append(
            html.Li(f"Nicht auflösbare Ticker: {', '.join(quality.unresolved)}")
        )
    if quality.missing_cache:
        items.append(
            html.Li(f"Ticker ohne Kurscache: {', '.join(quality.missing_cache)}")
        )
    if quality.gaps:
        items.append(
            html.Li(
                "Datenlücken: "
                + ", ".join(f"{t} ({n} Tage)" for t, n in sorted(quality.gaps.items()))
            )
        )
    stand = (
        quality.fetched_at.strftime("%d.%m.%Y %H:%M")
        if quality.fetched_at
        else "unbekannt"
    )
    return [
        section_header("Datenqualität", f"Cache-Stand: {stand}"),
        html.Ul(items) if items else html.P("Keine Auffälligkeiten."),
        html.P(
            [
                "Cache aktualisieren: ",
                html.Code(_UPDATE_CMD),
                " · Markdown-Report: ",
                html.Code("python -m app.tools.risk_report report"),
            ],
            className="ms-page-subtitle",
        ),
    ]


def layout(**_) -> html.Div:
    # Positionen als uids (bei Ticker-Kollisionen eindeutig; für alle
    # anderen identisch zum Ticker) — konsistent zu portfolio_weights().
    resolved = STATE.resolve_portfolio()
    tickers = resolved["uid"].astype(str).tolist() if not resolved.empty else []
    if not tickers:
        return html.Div(
            [
                page_title("Risiko & Benchmark"),
                _hint_card(
                    "Kein M&S-Portfolio vorhanden — bitte zuerst auf "
                    "„M&S Portfolio“ eine Watchlist importieren."
                ),
            ]
        )

    try:
        res = compute_risk_report(
            tickers,
            STATE.portfolio_weights(),
            STATE.settings,
            STATE.scored,
            date.today(),
        )
    except ValueError as exc:
        return html.Div(
            [
                page_title("Risiko & Benchmark"),
                _hint_card(str(exc)),
            ]
        )

    return html.Div(
        [
            page_title(
                "Risiko & Benchmark",
                f"Benchmark {res['benchmark']} · EUR-Sicht ohne Currency-"
                "Hedging · Interne Analyse, keine Anlageberatung; "
                "renditebasierte Schätzung, rückwärtsgerichtet.",
            ),
            _kpis(res),
            section_header(
                "Rollierender Tracking Error",
                "Std(aktive Tagesrendite) × √252 — Fenster 1 und 3 Jahre.",
            ),
            _te_chart(res),
            *_mcte_section(res),
            *_sector_chart(res),
            *_scenario_section(res),
            *_shock_section(res),
            *_quality_section(res),
        ]
    )


register_page(
    __name__,
    path="/risiko",
    name="Risiko & Benchmark",
    layout=layout,
)
