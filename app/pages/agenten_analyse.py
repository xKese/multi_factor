"""Agenten-Analyse — Ad-hoc-Tiefenanalyse und Sitzungs-Status.

Umsetzung des Design-Handoffs „Agenten-Tiefenanalyse" (Screen 2b):
Such-Karte mit klickbarer Treffer-Liste (Symbol-Suche des TradingAgents-
Service, inkl. Universum-Kennung), daneben die Kompakt-Status-Karten der
Sitzungs-Jobs, darunter die Verlaufstabelle im ``ms-toptable``-Stil mit
Agenten-Rating, Quant-Score und Δ-Quant-Einstufung.

Die Job-Registry in ``agents_client`` ist prozess-global — hier erscheinen
alle Läufe der Sitzung, egal ob sie hier oder in der Einzelanalyse
gestartet wurden.
"""

from __future__ import annotations

import json

import dash_bootstrap_components as dbc
import pandas as pd
from dash import (
    ALL,
    Input,
    Output,
    State,
    callback,
    callback_context,
    dcc,
    html,
    no_update,
    register_page,
)
from dash.exceptions import PreventUpdate

from app.core import agents_client, persistence, ticker_mapping
from app.core.state import STATE
from app.ui.agent_report import (
    compact_status_card,
    delta_quant,
    fmt_local_dt,
    rating_badge,
)


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                "Tiefenanalyse",
                                className="ms-eyebrow",
                                style={"color": "var(--ms-gold)"},
                            ),
                            html.H2("Agenten-Analyse"),
                        ]
                    ),
                    html.Div(
                        "Ad-hoc — auch außerhalb des Koyfin-Universums",
                        className="ms-meta",
                    ),
                ],
                className="ms-dash-section",
            ),
            dcc.Store(id="aa-selected"),
            dcc.Store(id="aa-results"),
            dcc.Store(id="aa-jobs-fp"),
            dcc.Interval(id="aa-poll", interval=3000, disabled=False),
            html.Div(
                [
                    html.Div(
                        [
                            html.H3("Titel suchen & analysieren", className="ms-card-h"),
                            html.Div(
                                [
                                    dbc.Input(
                                        id="aa-query",
                                        placeholder=(
                                            "⌕  Name oder Ticker (z. B. Mercedes, "
                                            "MBG.F, AAPL) …"
                                        ),
                                        type="text",
                                        debounce=True,
                                    ),
                                    html.Button(
                                        "Suchen",
                                        id="aa-search",
                                        n_clicks=0,
                                        className="ms-agent-btn-primary",
                                    ),
                                ],
                                className="ms-agent-searchrow",
                            ),
                            html.Div(
                                id="aa-search-note",
                                className="small text-warning",
                            ),
                            html.Div(id="aa-hits"),
                            html.Div(
                                [
                                    html.Span(
                                        "Titel im Universum erhalten den Quant-Score "
                                        "automatisch als Vorab-Rating.",
                                        className="ms-agent-search-hint",
                                    ),
                                    html.Button(
                                        "Tiefenanalyse starten",
                                        id="aa-start",
                                        n_clicks=0,
                                        className="ms-agent-btn-primary",
                                        style={"whiteSpace": "nowrap"},
                                    ),
                                ],
                                className="ms-agent-search-foot",
                            ),
                        ],
                        className="ms-card",
                        style={"gap": "14px"},
                    ),
                    html.Div(id="aa-status"),
                ],
                className="ms-agent-adhoc-grid mb-4",
            ),
            html.Div(
                [
                    html.Div(
                        [
                            html.Div(
                                "Verlauf",
                                className="ms-eyebrow",
                                style={"color": "var(--ms-gold)"},
                            ),
                            html.H2("Gespeicherte Agenten-Analysen"),
                        ]
                    ),
                    html.Div(id="aa-history-count", className="ms-meta"),
                ],
                className="ms-dash-section",
            ),
            html.Div(id="aa-history"),
            # Sammelreport-Export aus der Verlaufstabelle — statisch, damit
            # die Outputs die Tabellen-Re-Renders überleben.
            dcc.Download(id="aa-pdf-download"),
            html.Div(id="aa-pdf-error", className="ms-agent-pdf-error mt-2"),
        ]
    )


# ── Symbol-Suche mit Treffer-Liste (Design 2b) ─────────────────────────────

def _universe_tag(symbol: str) -> tuple[str, bool]:
    """Universum-Kennung eines Treffers: ``(label, im_universum)``.

    Basis-Ticker (ohne Börsen-Suffix) gegen ``STATE.scored`` prüfen.
    """
    base = (symbol or "").split(".")[0].upper()
    if base and not STATE.scored.empty:
        row = STATE.scored.loc[STATE.scored["ticker"].str.upper() == base]
        if not row.empty:
            score = row.iloc[0].get("total_score")
            if pd.notna(score):
                return (
                    f"Im Universum · Quant {f'{float(score):.1f}'.replace('.', ',')}",
                    True,
                )
            return "Im Universum", True
    return "Ad-hoc", False


def _hit_row(entry: dict, selected: str | None) -> html.Div:
    symbol = entry.get("symbol") or ""
    tag_label, in_uni = _universe_tag(symbol)
    classes = "ms-agent-hit"
    if selected and symbol == selected:
        classes += " is-selected"
    return html.Div(
        [
            html.Span(symbol, className="ms-agent-hit-sym"),
            html.Span(entry.get("name") or "?", className="ms-agent-hit-name"),
            html.Span(entry.get("region") or "", className="ms-agent-hit-region"),
            html.Span(
                tag_label,
                className="ms-agent-hit-tag" + (" is-uni" if in_uni else ""),
            ),
        ],
        id={"type": "aa-hit", "symbol": symbol},
        n_clicks=0,
        className=classes,
    )


def _hits_list(results: list[dict], selected: str | None) -> html.Div:
    rows = [_hit_row(r, selected) for r in results if r.get("symbol")]
    if not rows:
        return html.Div()
    return html.Div(rows, className="ms-agent-hits")


def _search_results(term: str) -> tuple[list[dict], str]:
    """Treffer der Symbol-Suche inkl. Freitext-Fallback für Tickereingaben."""
    results, note = agents_client.symbol_search(term)
    results = list(results or [])
    upper = term.strip().upper()
    known = {r.get("symbol") for r in results}
    ticker_like = bool(ticker_mapping._TICKER_RE.match(upper)) and (
        len(upper) <= 6 or "." in upper or "-" in upper
    )
    if upper and upper not in known and ticker_like:
        results.insert(0, {"symbol": upper, "name": "Direkt übernehmen", "region": ""})
    if not results and not note:
        note = "Keine Treffer — tickerartige Eingaben können direkt gestartet werden."
    return results[:10], note or ""


@callback(
    Output("aa-hits", "children"),
    Output("aa-results", "data"),
    Output("aa-selected", "data"),
    Output("aa-search-note", "children"),
    Output("aa-start", "children"),
    Input("aa-search", "n_clicks"),
    Input("aa-query", "n_submit"),
    Input("aa-query", "value"),
    State("aa-selected", "data"),
    prevent_initial_call=True,
)
def _search(_clicks, _submit, query, selected):
    term = (query or "").strip()
    if len(term) < 3:
        raise PreventUpdate
    results, note = _search_results(term)
    # Auswahl vorbelegen: bisherige Auswahl behalten, wenn noch in den
    # Treffern, sonst erster Treffer.
    symbols = [r.get("symbol") for r in results]
    if selected not in symbols:
        selected = symbols[0] if symbols else None
    label = (
        f"Tiefenanalyse starten · {selected}" if selected else "Tiefenanalyse starten"
    )
    return _hits_list(results, selected), results, selected, note, label


@callback(
    Output("aa-hits", "children", allow_duplicate=True),
    Output("aa-selected", "data", allow_duplicate=True),
    Output("aa-start", "children", allow_duplicate=True),
    Input({"type": "aa-hit", "symbol": ALL}, "n_clicks"),
    State("aa-results", "data"),
    prevent_initial_call=True,
)
def _select_hit(n_clicks_list, results):
    trigger = callback_context.triggered_id
    if not trigger or not any(n_clicks_list or []):
        raise PreventUpdate
    symbol = trigger.get("symbol")
    if not symbol:
        raise PreventUpdate
    results = results or []
    return (
        _hits_list(results, symbol),
        symbol,
        f"Tiefenanalyse starten · {symbol}",
    )


# ── Sitzungs-Status (Kompakt-Karten) ───────────────────────────────────────

def _jobs_fingerprint(jobs: dict[str, dict]) -> str:
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
    status = job.get("status")
    if status == "running":
        return compact_status_card(job.get("agents_ticker") or ticker, job)
    if status == "error":
        return html.Div(
            [
                html.H3(f"Fehlgeschlagen · {ticker}", className="ms-card-h"),
                html.Div(
                    [
                        html.Span("⚠"),
                        html.Span(job.get("error") or "Unbekannter Fehler"),
                    ],
                    className="ms-agent-warnstrip",
                    style={"marginTop": "0"},
                ),
            ],
            className="ms-card mb-3",
            style={"gap": "10px"},
        )
    # done
    analysis = persistence.load_agent_analysis(ticker)
    return html.Div(
        [
            html.H3(f"Abgeschlossen · {ticker}", className="ms-card-h"),
            html.Div(
                [
                    rating_badge(analysis.get("rating") if analysis else None),
                    dcc.Link(
                        "Einzelanalyse ›",
                        href=f"/einzelanalyse?ticker={ticker}",
                        className="ms-agent-histlink ms-2",
                    )
                    if analysis and analysis.get("in_universe")
                    else html.Span(),
                ],
                className="d-flex align-items-center",
            ),
        ],
        className="ms-card mb-3",
        style={"gap": "10px"},
    )


def _status_panel(jobs: dict[str, dict]) -> html.Div:
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
    State("aa-selected", "data"),
    State("aa-query", "value"),
    prevent_initial_call=True,
)
def _start(n_clicks, selected, query):
    if not n_clicks:
        raise PreventUpdate
    ticker = (selected or "").strip().upper() or (query or "").strip().upper()
    if not ticker:
        return no_update, no_update, "Bitte einen Titel suchen und auswählen."

    factor_context = None
    in_universe = False
    if not STATE.scored.empty:
        rows = STATE.scored.loc[STATE.scored["ticker"] == ticker]
        if rows.empty:
            base = ticker.split(".")[0]
            rows = STATE.scored.loc[STATE.scored["ticker"].str.upper() == base]
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


# ── Verlaufstabelle (Design 2b) ────────────────────────────────────────────

def _delta_cell(row) -> html.Td:
    fc = None
    raw = row.get("factor_context_json")
    if isinstance(raw, str) and raw:
        try:
            fc = json.loads(raw)
        except json.JSONDecodeError:
            fc = None
    delta = delta_quant(row.get("rating"), fc)
    if delta is None:
        return html.Td("–", className="ms-tt-muted")
    label, tone = delta
    return html.Td(html.Span(label, className=f"ms-agent-delta is-{tone}"))


def _name_of(ticker: str) -> str:
    if not STATE.scored.empty:
        row = STATE.scored.loc[STATE.scored["ticker"] == str(ticker)]
        if not row.empty:
            return str(row.iloc[0].get("name") or "–")
    return "–"


def _history_table(df: pd.DataFrame) -> html.Div:
    if df.empty:
        return html.Div(
            "Noch keine Agenten-Analysen gespeichert.",
            className="ms-tt-muted",
            style={"padding": "16px"},
        )

    rows = []
    for _, r in df.iterrows():
        created = r.get("created_at")
        created_label = fmt_local_dt(created)

        total = r.get("total_score")
        if total is not None and not pd.isna(total):
            cls = str(r.get("classification") or "").split("·")[0].strip()
            quant_cell = html.Td(
                [
                    f"{float(total):.1f}".replace(".", ","),
                    html.Span(f" ({cls})" if cls else "", className="ms-tt-muted"),
                ],
                className="is-num",
            )
        else:
            quant_cell = html.Td("–", className="is-num ms-tt-muted")

        in_uni = bool(r.get("in_universe"))
        ticker = str(r["ticker"])
        rows.append(
            html.Tr(
                [
                    html.Td(
                        html.A(
                            ticker,
                            href=f"/einzelanalyse?ticker={ticker}",
                            className="ms-tt-tk",
                        )
                        if in_uni
                        else html.Span(ticker, className="ms-agent-hit-sym"),
                    ),
                    html.Td(_name_of(ticker)),
                    html.Td(rating_badge(r.get("rating"), label=None)),
                    quant_cell,
                    _delta_cell(r),
                    html.Td(str(r.get("provider") or "–"), className="ms-tt-muted"),
                    html.Td("Ja" if in_uni else "Ad-hoc", className="ms-tt-muted"),
                    html.Td(created_label, className="is-num ms-tt-muted"),
                    html.Td(
                        html.Div(
                            [
                                html.A(
                                    "Einzelanalyse ›",
                                    href=f"/einzelanalyse?ticker={ticker}",
                                    className="ms-agent-histlink",
                                )
                                if in_uni
                                else None,
                                # Sammelreport auch für Ad-hoc-Titel — das
                                # PDF degradiert ohne Quant-Kontext von
                                # selbst (keine Score-Blöcke, kein Chip).
                                html.Button(
                                    "PDF ›",
                                    id={"type": "aa-hist-pdf", "ticker": ticker},
                                    n_clicks=0,
                                    className=(
                                        "ms-agent-histlink ms-agent-histlink-btn"
                                    ),
                                    title="Alle Berichte als PDF",
                                ),
                            ],
                            className="ms-agent-histactions",
                        ),
                    ),
                ]
            )
        )

    return html.Div(
        html.Table(
            [
                html.Thead(
                    html.Tr(
                        [
                            html.Th("Ticker"),
                            html.Th("Name"),
                            html.Th("Agenten-Rating"),
                            html.Th("Quant-Score", className="is-num"),
                            html.Th("Δ Quant"),
                            html.Th("Provider"),
                            html.Th("Universum"),
                            html.Th("Datum", className="is-num"),
                            html.Th(""),
                        ]
                    )
                ),
                html.Tbody(rows),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )


@callback(
    Output("aa-history", "children"),
    Output("aa-history-count", "children"),
    Input("aa-jobs-fp", "data"),
)
def _history(_fp):
    # Rendert initial und bei jedem Job-Zustandswechsel (Start/Fortschritt/
    # Abschluss) neu — Abschlüsse erscheinen sofort im Verlauf.
    df = persistence.list_agent_analyses()
    count = f"{len(df)} Analysen" if len(df) != 1 else "1 Analyse"
    return _history_table(df), count


@callback(
    Output("aa-pdf-download", "data"),
    Output("aa-pdf-error", "children"),
    Input({"type": "aa-hist-pdf", "ticker": ALL}, "n_clicks"),
    prevent_initial_call=True,
)
def _export_history_pdf(n_clicks_list):
    """Sammelreport „Alle Berichte" aus einer Verlaufszeile als PDF."""
    trigger = callback_context.triggered_id
    # Re-Mount-Guard: die Tabelle wird bei jedem Job-Fingerprint neu
    # gerendert — die Buttons feuern dann mit n_clicks=0.
    if not trigger or not any(n_clicks_list or []):
        raise PreventUpdate
    ticker = trigger.get("ticker")
    analysis = persistence.load_agent_analysis(ticker) if ticker else None
    if not analysis:
        return no_update, f"Keine gespeicherte Analyse für {ticker} gefunden."

    try:
        from app.core.pdf_export import FactsheetRenderError, render_full_pdf
    except Exception as exc:  # pragma: no cover — import-time issues
        return no_update, f"PDF-Modul nicht verfügbar: {exc!s}"

    try:
        pdf_bytes, filename = render_full_pdf(analysis, STATE.scored)
    except (FactsheetRenderError, ValueError) as exc:
        return no_update, str(exc)

    return (
        dcc.send_bytes(lambda buf: buf.write(pdf_bytes), filename=filename),
        "",
    )


register_page(__name__, path="/agenten-analyse", name="Agenten-Analyse", layout=layout)
