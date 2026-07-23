"""Agenten-Analyse — Ad-hoc-Tiefenanalyse und globale Status-Übersicht.

Erlaubt TradingAgents-Analysen auch für Titel, die NICHT im Koyfin-Universum
der Multi-Faktor-Bewertung enthalten sind, und zeigt zusätzlich den Status
ALLER Läufe dieser Sitzung — unabhängig davon, ob sie hier oder in der
Einzelanalyse gestartet wurden (die Job-Registry in ``agents_client`` ist
prozess-global). Wie in der TradingAgents-WebUI ist live sichtbar, welcher
Agent gerade arbeitet.

Darunter: Verlauf aller gespeicherten Agenten-Analysen (Universum + Ad-hoc).
"""

from __future__ import annotations

import json

import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, dcc, html, no_update, register_page
from dash.exceptions import PreventUpdate

from app.core import agents_client, persistence
from app.core.state import STATE
from app.ui.agent_report import progress_checklist, rating_badge, result_view


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Tiefenanalyse", className="ms-eyebrow"),
                            html.H2("Agenten-Analyse (Ad-hoc)"),
                        ]
                    ),
                    html.Div("Auch außerhalb des Koyfin-Universums", className="ms-meta"),
                ],
                className="ms-dash-section",
            ),
            html.Div(
                [
                    html.P(
                        "Beliebigen Titel per Symbol-Suche finden (liefert das "
                        "von yfinance/Alpha Vantage benötigte Format, z. B. "
                        "MBG.F für Frankfurt) und von den TradingAgents-"
                        "LLM-Agenten analysieren lassen. Für Titel im "
                        "Koyfin-Universum wird der Quant-Score automatisch als "
                        "Vorab-Rating mitgegeben.",
                        className="small ms-tt-muted",
                    ),
                    html.Div(
                        [
                            dbc.Input(
                                id="aa-query",
                                placeholder="Name oder Ticker (z. B. Mercedes, MBG.F, AAPL) …",
                                type="text",
                                debounce=True,
                                style={"maxWidth": "420px"},
                            ),
                            dbc.Button(
                                "Suchen",
                                id="aa-search",
                                color="dark",
                                outline=True,
                                size="sm",
                                n_clicks=0,
                                className="ms-2",
                            ),
                        ],
                        className="d-flex align-items-center mb-2",
                    ),
                    html.Div(id="aa-search-note", className="small text-warning mb-1"),
                    dbc.RadioItems(id="aa-symbol-choice", options=[]),
                    html.Div(
                        dbc.Button(
                            "Tiefenanalyse starten",
                            id="aa-start",
                            color="dark",
                            size="sm",
                            n_clicks=0,
                        ),
                        className="mt-2",
                    ),
                ],
                className="ms-card p-3 mb-3",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Live", className="ms-eyebrow"),
                            html.H2("Laufende & aktuelle Analysen"),
                        ]
                    ),
                    html.Div(
                        "Alle Läufe dieser Sitzung, egal wo gestartet",
                        className="ms-meta",
                    ),
                ],
                className="ms-dash-section",
            ),
            dcc.Store(id="aa-jobs-fp"),
            dcc.Interval(id="aa-poll", interval=3000, disabled=False),
            html.Div(id="aa-status", className="mb-4"),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div("Verlauf", className="ms-eyebrow"),
                            html.H2("Gespeicherte Agenten-Analysen"),
                        ]
                    ),
                ],
                className="ms-dash-section",
            ),
            html.Div(id="aa-history"),
        ]
    )


# ── Suche ──────────────────────────────────────────────────────────────────

@callback(
    Output("aa-symbol-choice", "options"),
    Output("aa-symbol-choice", "value"),
    Output("aa-search-note", "children"),
    Input("aa-search", "n_clicks"),
    Input("aa-query", "n_submit"),
    State("aa-query", "value"),
    prevent_initial_call=True,
)
def _search(_clicks, _submit, query: str | None):
    term = (query or "").strip()
    if len(term) < 2:
        return [], None, "Bitte mindestens 2 Zeichen eingeben."
    results, note = agents_client.symbol_search(term)
    options = [
        {
            "label": f"{r.get('symbol')} — {r.get('name') or '?'}"
            + (f" ({r.get('region')})" if r.get("region") else ""),
            "value": r.get("symbol"),
        }
        for r in results[:10]
        if r.get("symbol")
    ]
    default = options[0]["value"] if options else None
    if not options and not note:
        note = "Keine Treffer — der Ticker kann trotzdem direkt eingegeben werden."
    return options, default, note or ""


# ── Globale Status-Übersicht ───────────────────────────────────────────────

def _jobs_fingerprint(jobs: dict[str, dict]) -> str:
    """Kompakter Zustands-Fingerprint: nur bei Änderung wird neu gerendert."""
    return json.dumps(
        [
            [
                t,
                j.get("status"),
                j.get("stage"),
                sorted((j.get("agent_states") or {}).items()),
            ]
            for t, j in jobs.items()
        ],
        ensure_ascii=False,
    )


def _job_card(ticker: str, job: dict) -> html.Div:
    agents_ticker = job.get("agents_ticker")
    title_bits: list = [html.Strong(ticker)]
    if agents_ticker and agents_ticker != ticker:
        title_bits.append(
            html.Span(f" → {agents_ticker}", className="ms-tt-muted small")
        )

    status = job.get("status")
    if status == "running":
        body: list = [progress_checklist(job)]
    elif status == "error":
        body = [
            dbc.Alert(
                f"Analyse fehlgeschlagen: {job.get('error') or 'Unbekannter Fehler'}",
                color="danger",
                className="small mb-0",
            )
        ]
    else:  # done
        analysis = persistence.load_agent_analysis(ticker)
        if analysis:
            body = [result_view(analysis)]
        else:
            body = [
                html.Div(
                    [
                        rating_badge(None),
                        html.Span(
                            " Abgeschlossen, aber kein gespeichertes Ergebnis "
                            "gefunden.",
                            className="small ms-2",
                        ),
                    ]
                )
            ]

    return html.Div(
        [html.Div(title_bits, className="mb-2"), *body],
        className="ms-card p-3 mb-3",
    )


def _status_panel(jobs: dict[str, dict]) -> html.Div:
    if not jobs:
        return html.Div(
            "In dieser Sitzung wurden noch keine Analysen gestartet. "
            "Läufe aus der Einzelanalyse erscheinen hier ebenfalls. "
            "(Nach einem Neustart der App ist diese Live-Ansicht leer — "
            "abgeschlossene Analysen stehen dauerhaft im Verlauf unten.)",
            className="ms-tt-muted",
            style={"padding": "16px"},
        )
    return html.Div([_job_card(t, j) for t, j in jobs.items()])


@callback(
    Output("aa-status", "children"),
    Output("aa-jobs-fp", "data"),
    Input("aa-poll", "n_intervals"),
    State("aa-jobs-fp", "data"),
)
def _poll(_n, prev_fp):
    jobs = agents_client.list_jobs()
    fp = _jobs_fingerprint(jobs)
    if fp == prev_fp:
        return no_update, no_update
    return _status_panel(jobs), fp


# ── Start ──────────────────────────────────────────────────────────────────

@callback(
    Output("aa-status", "children", allow_duplicate=True),
    Output("aa-jobs-fp", "data", allow_duplicate=True),
    Output("aa-search-note", "children", allow_duplicate=True),
    Input("aa-start", "n_clicks"),
    State("aa-symbol-choice", "value"),
    State("aa-query", "value"),
    prevent_initial_call=True,
)
def _start(n_clicks, choice, query):
    if not n_clicks:
        raise PreventUpdate
    ticker = (choice or "").strip().upper() or (query or "").strip().upper()
    if not ticker:
        return (
            no_update,
            no_update,
            "Bitte einen Titel suchen/auswählen oder einen Ticker eingeben.",
        )

    # Titel aus dem Universum bekommen ihren Quant-Score als Vorab-Rating mit.
    factor_context = None
    in_universe = False
    if not STATE.scored.empty:
        rows = STATE.scored.loc[STATE.scored["ticker"] == ticker]
        if not rows.empty:
            factor_context = agents_client.build_factor_context(rows.iloc[0])
            in_universe = True

    ok, msg = agents_client.start_analysis(
        ticker,
        ticker,
        STATE.settings,
        factor_context=factor_context,
        in_universe=in_universe,
    )
    if not ok:
        return no_update, no_update, msg

    jobs = agents_client.list_jobs()
    return _status_panel(jobs), _jobs_fingerprint(jobs), ""


# ── Verlauf ────────────────────────────────────────────────────────────────

def _history_table(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div(
            "Noch keine Agenten-Analysen gespeichert.",
            className="ms-tt-muted",
            style={"padding": "16px"},
        )

    header = html.Thead(
        html.Tr(
            [
                html.Th("Ticker"),
                html.Th("Rating"),
                html.Th("Quant-Score"),
                html.Th("Provider"),
                html.Th("Universum"),
                html.Th("Datum"),
                html.Th(""),
            ]
        )
    )
    rows = []
    for _, r in df.iterrows():
        created = r.get("created_at")
        try:
            created_label = pd.to_datetime(created).strftime("%d.%m.%Y %H:%M")
        except (ValueError, TypeError):
            created_label = str(created or "–")
        total = r.get("total_score")
        quant = (
            f"{float(total):.1f} ({r.get('classification') or '–'})"
            if total is not None and not pd.isna(total)
            else "–"
        )
        in_uni = bool(r.get("in_universe"))
        link = (
            dcc.Link(
                "Einzelanalyse",
                href=f"/einzelanalyse?ticker={r['ticker']}",
                className="ms-nav-link",
            )
            if in_uni
            else html.Span("")
        )
        rows.append(
            html.Tr(
                [
                    html.Td(html.Strong(str(r["ticker"]))),
                    html.Td(rating_badge(r.get("rating"))),
                    html.Td(quant),
                    html.Td(str(r.get("provider") or "–")),
                    html.Td("Ja" if in_uni else "Ad-hoc"),
                    html.Td(created_label),
                    html.Td(link),
                ]
            )
        )
    return html.Div(
        dbc.Table(
            [header, html.Tbody(rows)],
            hover=True,
            responsive=True,
            className="ms-table",
        ),
        className="ms-card",
    )


@callback(Output("aa-history", "children"), Input("aa-jobs-fp", "data"))
def _history(_fp):
    # Rendert initial und immer dann neu, wenn sich der Job-Zustand ändert
    # (Start, Agenten-Fortschritt, Abschluss) — Abschlüsse landen im Verlauf.
    return _history_table(persistence.list_agent_analyses())


register_page(__name__, path="/agenten-analyse", name="Agenten-Analyse", layout=layout)
