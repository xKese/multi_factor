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

from app.core import agents_client, persistence, ticker_mapping
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
                        dcc.Dropdown(
                            id="aa-symbol",
                            options=[],
                            placeholder=(
                                "Name oder Ticker tippen (z. B. Mercedes, "
                                "MBG.F, AAPL) …"
                            ),
                            searchable=True,
                            clearable=True,
                        ),
                        style={"maxWidth": "480px"},
                        className="mb-1",
                    ),
                    html.Div(id="aa-search-note", className="small text-warning mb-1"),
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


# ── Live-Symbol-Suche (Autocomplete, analog TradingAgents-WebUI) ──────────

def _symbol_options(
    results: list[dict], term: str, current: str | None = None
) -> list[dict]:
    """Dropdown-Optionen aus Symbol-Such-Treffern bauen.

    Sieht der Suchbegriff selbst wie ein Ticker aus, bleibt er als erste
    Option direkt wählbar (Freitext-Fallback ohne AV-Key/Treffer). Eine
    bereits getroffene Auswahl bleibt erhalten, sonst verwirft Dash sie
    beim Options-Wechsel.
    """
    options = [
        {
            "label": f"{r.get('symbol')} — {r.get('name') or '?'}"
            + (f" ({r.get('region')})" if r.get("region") else ""),
            "value": r.get("symbol"),
        }
        for r in results[:10]
        if r.get("symbol")
    ]

    seen = {o["value"] for o in options}
    upper = (term or "").strip().upper()
    # Tickertypisch: kurz (<= 6 Zeichen) oder mit Börsen-Suffix/Klassen-
    # Trenner — Firmennamen wie "MERCEDES" sollen keine Pseudo-Option werden.
    ticker_like = bool(ticker_mapping._TICKER_RE.match(upper)) and (
        len(upper) <= 6 or "." in upper or "-" in upper
    )
    if upper and upper not in seen and ticker_like:
        options.insert(
            0, {"label": f"{upper} — direkt übernehmen", "value": upper}
        )
        seen.add(upper)

    if current and current not in seen:
        options.append({"label": current, "value": current})
    return options


@callback(
    Output("aa-symbol", "options"),
    Output("aa-search-note", "children"),
    Input("aa-symbol", "search_value"),
    State("aa-symbol", "value"),
    prevent_initial_call=True,
)
def _search(search_value: str | None, current: str | None):
    term = (search_value or "").strip()
    if len(term) < 3:
        raise PreventUpdate
    results, note = agents_client.symbol_search(term)
    options = _symbol_options(results, term, current)
    if not results and not note:
        note = (
            "Keine Treffer — tickerartige Eingaben können direkt "
            "übernommen werden."
        )
    return options, note or ""


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
    # Ohne Läufe bleibt der Bereich unsichtbar — der globale Status lebt im
    # Kopfzeilen-Chip (app/main.py); hier erscheinen Karten nur bei Bedarf
    # (laufende Jobs dieser Sitzung sowie deren Ergebnisse/Fehler).
    if not jobs:
        return html.Div()
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
    State("aa-symbol", "value"),
    prevent_initial_call=True,
)
def _start(n_clicks, symbol):
    if not n_clicks:
        raise PreventUpdate
    ticker = (symbol or "").strip().upper()
    if not ticker:
        return (
            no_update,
            no_update,
            "Bitte einen Titel per Suche auswählen.",
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
