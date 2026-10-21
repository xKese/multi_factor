"""Seite „KI-Analyst": Status des Analyst-Agenten, Trends & Profiteure,
Themen, Vorschläge, Firmen-Analysen, Ereignis-Feed und Läufe."""

from __future__ import annotations

import os

import dash_bootstrap_components as dbc
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

from app.core.analyst import scheduler, store
from app.core.diagnostics import diags_from_json
from app.core.state import STATE
from app.ui.agent_report import first_sentences, fmt_local_dt
from app.ui.analyst import (
    PAGE_PATH,
    fmt_num as _fmt,
    kind_label,
    pricing_chip,
    role_badge,
    suggestion_badge,
    synthesis_blocks,
    theme_kind_badge,
    ticker_link as _ticker_link,
)
from app.ui.theme import diagnostics_panel, zone_badge

_EMPTY_STYLE = {"padding": "16px"}


def _section(eyebrow: str, title: str, meta=None) -> html.Div:
    return html.Div(
        [
            html.Div(
                [html.Div(eyebrow, className="ms-eyebrow", style={"color": "var(--ms-gold)"}), html.H2(title)]
            ),
            # Dash-Komponenten sind bei len()==0 falsy — daher kein ``meta or ""``.
            html.Div(meta if meta is not None else "", className="ms-meta"),
        ],
        className="ms-dash-section",
    )


def layout(**_) -> html.Div:
    s = STATE.settings
    return html.Div(
        [
            _section(
                "Laufender Analyst",
                "KI-Analyst",
                f"Modell {s.analyst_model} · Scope {s.analyst_scope} · "
                f"Intervall {s.analyst_interval_minutes} min",
            ),
            dcc.Store(id="ka-fp"),
            dcc.Interval(id="ka-poll", interval=3000, disabled=False),
            html.Div(id="ka-status", className="mb-4"),
            html.Div(id="ka-run-msg", className="small text-warning mb-2"),
            _section("Universum", "Trends & Profiteure", html.Span(id="ka-trends-count")),
            html.Div(
                dbc.Checklist(
                    id="ka-trend-early",
                    options=[{"label": "nur früh erkennbare Profiteure", "value": "early"}],
                    value=[],
                    switch=True,
                    className="mb-2",
                )
            ),
            html.Div(id="ka-trends"),
            _section("Universum", "Themen", html.Span(id="ka-themes-count")),
            html.Div(id="ka-themes"),
            _section("Titel", "Vorschläge", html.Span(id="ka-suggestions-count")),
            html.Div(id="ka-suggestions"),
            _section("Titel", "Firmen-Analysen", html.Span(id="ka-analyses-count")),
            html.Div(id="ka-analyses"),
            _section(
                "Verlauf",
                "Ereignis-Feed",
                dbc.Select(
                    id="ka-kind-filter",
                    options=[{"label": "Alle Ereignisse", "value": ""}]
                    + [{"label": kind_label(k), "value": k} for k in ("earnings", "earnings_upcoming", "transcript", "insider", "news", "zone", "score")],
                    value="",
                    size="sm",
                    style={"maxWidth": "260px"},
                ),
            ),
            html.Div(id="ka-events"),
            _section("Verlauf", "Läufe", html.Span(id="ka-runs-count")),
            html.Div(id="ka-runs"),
        ]
    )


# ── Status ────────────────────────────────────────────────────────────────
def _key_hints() -> list:
    hints = []
    for env, label in (("OLLAMA_API_KEY", "Ollama Cloud"), ("ALPHAVANTAGE_API_KEY", "Alpha Vantage")):
        if not os.getenv(env, "").strip():
            hints.append(
                html.Div(
                    f"{env} ist nicht gesetzt — {label} ist deaktiviert. Key als Umgebungsvariable setzen.",
                    className="ms-agent-warnstrip",
                )
            )
    return hints


def _status_card(status: dict) -> html.Div:
    s = STATE.settings
    running = status.get("running")
    state_txt = "läuft …" if running else ("aktiv (Scheduler)" if s.analyst_enabled else "manuell (Scheduler aus)")
    rows = [
        ("Status", html.Span(state_txt, className="text-success" if running or s.analyst_enabled else "ms-tt-muted")),
        ("Letzter Lauf", f"{status.get('last_run_id') or '–'} · {status.get('last_status') or '–'} · {fmt_local_dt(status.get('last_finished_at')) if status.get('last_finished_at') else '–'}"),
        ("Nächster Lauf", fmt_local_dt(status.get("next_run_at")) if status.get("next_run_at") else "–"),
    ]
    if status.get("progress"):
        rows.append(("Fortschritt", status["progress"]))
    if status.get("last_error"):
        rows.append(("Fehler", html.Span(str(status["last_error"]), className="text-danger")))
    return html.Div(
        [
            html.H3("Analyst-Status", className="ms-card-h"),
            *_key_hints(),
            html.Table(
                html.Tbody([html.Tr([html.Td(k, className="ms-tt-muted", style={"paddingRight": "12px"}), html.Td(v)]) for k, v in rows]),
                className="mb-3",
            ),
            html.Div(
                [
                    html.Button("Jetzt ausführen", id="ka-run-now", n_clicks=0, className="ms-agent-btn-primary", disabled=bool(running)),
                    dcc.Link("Einstellungen ›", href="/einstellungen", className="ms-agent-search-hint ms-2"),
                ],
                className="d-flex align-items-center gap-3",
            ),
        ],
        className="ms-card",
        style={"gap": "10px"},
    )


@callback(
    Output("ka-status", "children"),
    Output("ka-fp", "data"),
    Input("ka-poll", "n_intervals"),
    State("ka-fp", "data"),
)
def _poll(_n, prev_fp):
    status = scheduler.get_status()
    runs = store.list_runs(limit=1)
    last_run = runs[0]["run_id"] if runs else ""
    fp = f"{status.get('status')}|{status.get('last_run_id')}|{status.get('progress')}|{last_run}"
    return _status_card(status), (fp if fp != prev_fp else no_update)


@callback(
    Output("ka-run-msg", "children"),
    Input("ka-run-now", "n_clicks"),
    prevent_initial_call=True,
)
def _run_now(n_clicks):
    if not n_clicks:
        raise PreventUpdate
    ok, msg = scheduler.trigger_now()
    return html.Span(msg, className="text-success" if ok else "text-warning")


# ── Trends ────────────────────────────────────────────────────────────────
def _trends_view(trends: list[dict], early_only: bool) -> html.Div:
    if not trends:
        return html.Div("Keine Trends im letzten Lauf.", className="ms-tt-muted", style=_EMPTY_STYLE)
    items = []
    for t in trends:
        links = t.get("links") or []
        if early_only:
            links = [l for l in links if l.get("pricing_quant") == "früh"]
        rows = [
            html.Tr(
                [
                    html.Td(_ticker_link(l.get("uid"), l.get("ticker"))),
                    html.Td(role_badge(l.get("role"))),
                    html.Td([pricing_chip(l.get("pricing_quant")), html.Span(f" (LLM: {l.get('pricing_llm') or '–'})", className="ms-tt-muted small")]),
                    html.Td(l.get("action") or "–"),
                    html.Td(" → ".join(str(r) for r in (l.get("reasoning") or [])) or "–", className="small"),
                ]
            )
            for l in links
        ]
        table = html.Table(
            [
                html.Thead(html.Tr([html.Th(h) for h in ("Ticker", "Rolle", "Eingepreist", "Aktion", "Begründungskette")])),
                html.Tbody(rows or [html.Tr(html.Td("keine Profiteure" + (" (Filter: nur früh)" if early_only else ""), colSpan=5, className="ms-tt-muted"))]),
            ],
            className="ms-toptable",
        )
        ev = ", ".join(f"{e.get('quelle')}: {e.get('referenz')}" for e in (t.get("evidence") or []) if isinstance(e, dict))
        items.append(
            dbc.AccordionItem(
                [
                    html.P(t.get("description") or ""),
                    html.Div(
                        [
                            html.Span(f"Art {t.get('kind')} · Horizont {t.get('horizon')} · Stärke {t.get('strength')}/5 · Konfidenz {_fmt(t.get('confidence'), 2)}", className="ms-tt-muted small"),
                            html.Span(f" · Industriegruppen: {', '.join(t.get('industry_groups') or []) or '–'}", className="ms-tt-muted small"),
                        ]
                    ),
                    html.Div(f"Evidenz: {ev or '–'}", className="small mb-2"),
                    html.Div(f"Verlierer-Hinweis: {t['loser_note']}", className="small mb-2") if t.get("loser_note") else None,
                    html.Div(table, className="ms-toptable-wrap"),
                ],
                title=f"{t.get('title')} ({len(links)} Profiteure)",
            )
        )
    return dbc.Accordion(items, start_collapsed=False, always_open=True, flush=True)


# ── Themen ────────────────────────────────────────────────────────────────
def _themes_view(themes: list[dict], links: list[dict]) -> html.Div:
    if not themes:
        return html.Div("Keine Themen im letzten Lauf.", className="ms-tt-muted", style=_EMPTY_STYLE)
    scored = STATE.scored
    cards = []
    for t in themes:
        tickers = []
        for uid in t.get("uids") or []:
            tk = uid
            if scored is not None and not scored.empty and "uid" in scored.columns:
                hit = scored[scored["uid"].astype(str) == str(uid)]
                if not hit.empty:
                    tk = str(hit.iloc[0].get("ticker") or uid)
            tickers.append(_ticker_link(uid, tk))
            tickers.append(" ")
        cards.append(
            html.Div(
                [
                    html.Div([html.Strong(t.get("title")), " ", theme_kind_badge(t.get("kind"), t.get("direction")), html.Span(f" Konfidenz {_fmt(t.get('confidence'), 2)}", className="ms-tt-muted small")]),
                    html.Div(t.get("description") or "", className="small mt-1"),
                    html.Div(["Betroffen: ", *tickers] if tickers else "Betroffen: –", className="small mt-1"),
                    html.Div(f"Bestätigung durch: {t.get('confirmation') or '–'}", className="small ms-tt-muted"),
                ],
                className="ms-card mb-2",
            )
        )
    if links:
        rows = [
            html.Tr([html.Td(_ticker_link(l.get("uid"), l.get("ticker") or l.get("uid"))), html.Td(_ticker_link(l.get("related_uid"), l.get("related_ticker") or l.get("related_name"))), html.Td(l.get("relation")), html.Td(l.get("context"), className="small")])
            for l in links
        ]
        cards.append(
            html.Div(
                [
                    html.H3("Firmenübergreifende Verknüpfungen", className="ms-card-h"),
                    html.Div(html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Titel", "Verbunden mit", "Beziehung", "Beleg")])), html.Tbody(rows)], className="ms-toptable"), className="ms-toptable-wrap"),
                ],
                className="ms-card",
            )
        )
    return html.Div(cards)


# ── Vorschläge ────────────────────────────────────────────────────────────
def _suggestions_table(rows: list[dict]) -> html.Div:
    if not rows:
        return html.Div("Noch keine Vorschläge — einen Lauf starten.", className="ms-tt-muted", style=_EMPTY_STYLE)
    body = []
    for r in rows:
        done = r.get("status") != "open"
        body.append(
            html.Tr(
                [
                    html.Td(_ticker_link(r.get("uid"), r.get("ticker"))),
                    html.Td(suggestion_badge(r, link=False)),
                    html.Td(_fmt(r.get("confidence"), 2), className="is-num"),
                    html.Td([zone_badge(r.get("quant_zone")), html.Span(f" {_fmt(r.get('quant_score'))}", className="ms-tt-muted small")]),
                    html.Td(r.get("quant_consistency") or "–"),
                    html.Td(f"{r.get('n_interplay') or 0} / {r.get('n_unpriced') or 0}", className="is-num"),
                    html.Td(first_sentences(r.get("rationale"), n=2, max_chars=220), className="small", title=str(r.get("rationale") or "")),
                    html.Td(fmt_local_dt(r.get("created_at")), className="ms-tt-muted small"),
                    html.Td(
                        [
                            html.Button("Erledigt", id={"type": "ka-sugg-done", "id": int(r["id"])}, n_clicks=0, className="btn btn-sm btn-outline-secondary me-1", disabled=done),
                            html.Button("Verwerfen", id={"type": "ka-sugg-dismiss", "id": int(r["id"])}, n_clicks=0, className="btn btn-sm btn-outline-secondary", disabled=done),
                        ]
                    ),
                ],
                className="ms-tt-muted" if done else None,
            )
        )
    return html.Div(
        html.Table(
            [
                html.Thead(html.Tr([html.Th(h) for h in ("Ticker", "Vorschlag", "Konfidenz", "Quant-Zone", "Konsistenz", "Zusammenspiel / Themen", "Begründung", "Datum", "")])),
                html.Tbody(body),
            ],
            className="ms-toptable",
        ),
        className="ms-toptable-wrap",
    )


# ── Firmen-Analysen ───────────────────────────────────────────────────────
def _analyses_view(run_id: str | None) -> html.Div:
    if not run_id:
        return html.Div("Noch keine Analysen.", className="ms-tt-muted", style=_EMPTY_STYLE)
    syntheses = store.list_insights(kind="synthesis", run_id=run_id, limit=100)
    if not syntheses:
        return html.Div("Im letzten Lauf keine Firmen-Synthesen (keine neuen Quellen).", className="ms-tt-muted", style=_EMPTY_STYLE)
    items = []
    for syn in syntheses:
        uid = syn.get("uid")
        tr = store.load_insight(uid, "transcript_summary")
        nd = store.load_insight(uid, "news_digest")
        blocks = []
        if tr and tr.get("content"):
            c = tr["content"]
            g = c.get("guidance") or {}
            blocks += [html.Strong(f"Earnings-Call {tr.get('period') or ''}"), html.P(c.get("zusammenfassung") or ""),
                       html.Div(f"Guidance: {g.get('aenderung')} ({g.get('details')}) · Ton {c.get('ton')}/5 · Vertrauen {c.get('vertrauen_management')}/5 · Quant-Abgleich: {(c.get('quant_abgleich') or {}).get('urteil')}", className="small mb-2")]
        if nd and nd.get("content"):
            blocks += [html.Strong("News-Digest"), html.P(nd["content"].get("zusammenfassung") or ""),
                       html.Div(f"Wesentlichkeit {nd['content'].get('wesentlichkeit')}/5", className="small mb-2")]
        content = syn.get("content")
        v = (content or {}).get("vorschlag") or {}
        blocks += synthesis_blocks(content, store.load_company_links_for(uid))
        title = f"{syn.get('ticker') or uid} — {v.get('typ') or 'ohne Vorschlag'}" if content else f"{syn.get('ticker') or uid} — nicht parsebar"
        items.append(dbc.AccordionItem(blocks, title=title))
    return dbc.Accordion(items, start_collapsed=True, always_open=True, flush=True)


# ── Ereignisse / Läufe ────────────────────────────────────────────────────
def _events_table(events: list[dict]) -> html.Div:
    if not events:
        return html.Div("Keine Ereignisse.", className="ms-tt-muted", style=_EMPTY_STYLE)
    rows = [
        html.Tr([html.Td(_ticker_link(e.get("uid"), e.get("ticker"))), html.Td(kind_label(e.get("kind"))), html.Td(e["event_date"].isoformat() if e.get("event_date") else "–"), html.Td(e.get("title"), className="small"), html.Td(e.get("run_id") or "–", className="ms-tt-muted small")])
        for e in events
    ]
    return html.Div(html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Ticker", "Art", "Datum", "Ereignis", "Lauf")])), html.Tbody(rows)], className="ms-toptable"), className="ms-toptable-wrap")


def _runs_table(runs: list[dict]) -> html.Div:
    if not runs:
        return html.Div("Noch keine Läufe.", className="ms-tt-muted", style=_EMPTY_STYLE)
    rows = [
        html.Tr([html.Td(r["run_id"]), html.Td(r.get("status") or "–"), html.Td(r.get("trigger") or "–"), html.Td(fmt_local_dt(r.get("started_at"))), html.Td(str(r.get("n_companies") or 0), className="is-num"), html.Td(str(r.get("n_events") or 0), className="is-num"), html.Td(str(r.get("n_llm_calls") or 0), className="is-num"), html.Td(str(r.get("n_av_calls") or 0), className="is-num"), html.Td(f"{r.get('n_themes') or 0} / {r.get('n_trends') or 0}", className="is-num"), html.Td(r.get("report_path") or "–", className="ms-tt-muted small")])
        for r in runs
    ]
    table = html.Div(html.Table([html.Thead(html.Tr([html.Th(h) for h in ("Lauf", "Status", "Auslöser", "Start", "Titel", "Ereignisse", "LLM", "AV", "Themen / Trends", "Report")])), html.Tbody(rows)], className="ms-toptable"), className="ms-toptable-wrap")
    diags = diags_from_json(runs[0].get("diagnostics_json"))
    return html.Div([table, html.Div(diagnostics_panel(diags, title="Diagnose (KI-Analyst, letzter Lauf)"), className="mt-3") if diags else None])


@callback(
    Output("ka-trends", "children"),
    Output("ka-trends-count", "children"),
    Output("ka-themes", "children"),
    Output("ka-themes-count", "children"),
    Output("ka-suggestions", "children"),
    Output("ka-suggestions-count", "children"),
    Output("ka-analyses", "children"),
    Output("ka-analyses-count", "children"),
    Output("ka-events", "children"),
    Output("ka-runs", "children"),
    Output("ka-runs-count", "children"),
    Input("ka-fp", "data"),
    Input("ka-trend-early", "value"),
    Input("ka-kind-filter", "value"),
)
def _tables(_fp, early, kind):
    trends = store.list_trends()
    themes = store.list_themes()
    links = store.list_company_links()
    cross = [l for l in links if l.get("source") == "themes"]
    suggestions = store.list_suggestions(limit=100)
    runs = store.list_runs(limit=20)
    last_run = runs[0]["run_id"] if runs else None
    n_syn = len(store.list_insights(kind="synthesis", run_id=last_run, limit=100)) if last_run else 0
    events = store.list_events(limit=100, kind=kind or None)
    return (
        _trends_view(trends, bool(early)),
        f"{len(trends)} Trends",
        _themes_view(themes, cross),
        f"{len(themes)} Themen",
        _suggestions_table(suggestions),
        f"{sum(1 for s in suggestions if s.get('status') == 'open')} offen von {len(suggestions)}",
        _analyses_view(last_run),
        f"{n_syn} Synthesen im letzten Lauf",
        _events_table(events),
        _runs_table(runs),
        f"{len(runs)} Läufe",
    )


@callback(
    Output("ka-fp", "data", allow_duplicate=True),
    Input({"type": "ka-sugg-done", "id": ALL}, "n_clicks"),
    Input({"type": "ka-sugg-dismiss", "id": ALL}, "n_clicks"),
    State("ka-fp", "data"),
    prevent_initial_call=True,
)
def _suggestion_action(done_clicks, dismiss_clicks, fp):
    trigger = callback_context.triggered_id
    # Re-Mount-Guard: nach jedem Re-Render feuern die Buttons mit n_clicks=0.
    if not trigger or not any((done_clicks or []) + (dismiss_clicks or [])):
        raise PreventUpdate
    status = "done" if trigger.get("type") == "ka-sugg-done" else "dismissed"
    try:
        store.set_suggestion_status(int(trigger.get("id")), status)
    except Exception:  # noqa: BLE001 — DB-Fehler: Tabelle bleibt wie sie ist
        raise PreventUpdate
    return f"{fp or ''}|{status}:{trigger.get('id')}"


register_page(__name__, path=PAGE_PATH, name="KI-Analyst", layout=layout)
