"""Agenten-Analyse — Ad-hoc-Tiefenanalyse für beliebige Ticker.

Erlaubt TradingAgents-Analysen auch für Titel, die NICHT im Koyfin-Universum
der Multi-Faktor-Bewertung enthalten sind. Die Symbol-Suche des Service
liefert direkt Yahoo-Dialekt-Ticker (inkl. europäischer Börsen-Suffixe wie
``MBG.F``), sodass kein manuelles Mapping nötig ist. Ohne Quant-Score wird
kein Faktor-Kontext mitgegeben — die Agenten analysieren „blind“.

Darunter: Verlauf aller gespeicherten Agenten-Analysen (Universum + Ad-hoc).
"""

from __future__ import annotations

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
            dcc.Store(id="aa-active-ticker"),
            dcc.Interval(id="aa-poll", interval=2500, disabled=True),
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


# ── Start & Fortschritt ────────────────────────────────────────────────────

@callback(
    Output("aa-status", "children"),
    Output("aa-poll", "disabled"),
    Output("aa-active-ticker", "data"),
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
            dbc.Alert(
                "Bitte einen Titel suchen/auswählen oder einen Ticker eingeben.",
                color="warning",
                className="small",
            ),
            True,
            no_update,
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
        return dbc.Alert(msg, color="warning", className="small"), True, no_update
    job = agents_client.get_status(ticker) or {}
    return (
        html.Div(progress_checklist(job), className="ms-card p-3"),
        False,
        ticker,
    )


@callback(
    Output("aa-status", "children", allow_duplicate=True),
    Output("aa-poll", "disabled", allow_duplicate=True),
    Input("aa-poll", "n_intervals"),
    State("aa-active-ticker", "data"),
    prevent_initial_call=True,
)
def _poll(_n, ticker):
    if not ticker:
        return no_update, True
    job = agents_client.get_status(ticker)
    if job is None:
        return no_update, True
    if job.get("status") == "running":
        return html.Div(progress_checklist(job), className="ms-card p-3"), False
    if job.get("status") == "error":
        return (
            dbc.Alert(
                f"Analyse fehlgeschlagen: {job.get('error') or 'Unbekannter Fehler'}",
                color="danger",
                className="small",
            ),
            True,
        )
    analysis = persistence.load_agent_analysis(ticker)
    if analysis is None:
        return (
            dbc.Alert(
                "Analyse abgeschlossen, aber kein gespeichertes Ergebnis "
                "gefunden.",
                color="warning",
                className="small",
            ),
            True,
        )
    return html.Div(result_view(analysis), className="ms-card p-3"), True


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


@callback(
    Output("aa-history", "children"),
    Input("aa-poll", "disabled"),
    Input("aa-start", "n_clicks"),
)
def _history(_disabled, _n):
    # Läuft initial und nach jedem Start/Abschluss (Poll-Umschaltung).
    return _history_table(persistence.list_agent_analyses())


register_page(__name__, path="/agenten-analyse", name="Agenten-Analyse", layout=layout)
