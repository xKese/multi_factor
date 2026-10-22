"""KI-Chat als Popup-Panel auf jeder Seite.

Global im App-Layout gemountet (``app/main.py``): schwebender Button unten
rechts (``ms-chat-fab``) und ein ``dbc.Offcanvas`` mit Sitzungsauswahl,
Seitenbezug, Nachrichtenliste, Eingabe und Schnellfragen. Die Antwort
entsteht im Hintergrund (``chat.start_answer``); ein ``dcc.Interval``
pollt nur, solange ein Job läuft (Fingerprint-Store gegen Re-Renders).

Reine Helfer (``parse_page_context``, ``render_messages``,
``quick_questions``) sind ohne laufende App testbar.
"""

from __future__ import annotations

import os
from urllib.parse import parse_qs

import dash_bootstrap_components as dbc
from dash import ALL, Input, Output, State, callback, ctx, dcc, html, no_update
from dash.exceptions import PreventUpdate

from app.core.analyst import chat, store
from app.core.state import STATE
from app.core.uid import row_by_uid
from app.ui.agent_report import fmt_local_dt

MAX_RENDERED_MESSAGES = 60
TOOL_PREVIEW_CHARS = 600


# --------------------------------------------------------------------------- #
# Reine Helfer
# --------------------------------------------------------------------------- #
def parse_page_context(pathname: str | None, search: str | None, scored) -> dict | None:
    """Titel-Bezug aus der URL (``?ticker=`` oder ``?uid=`` auf jeder Seite)."""
    if not search or scored is None or getattr(scored, "empty", True):
        return None
    qs = parse_qs(str(search).lstrip("?"))
    key = None
    for name in ("ticker", "uid"):
        vals = qs.get(name)
        if vals and vals[0]:
            key = vals[0]
            break
    if not key:
        return None
    row = row_by_uid(scored, key)
    if row is None:
        return None
    return {
        "uid": str(row.get("uid")),
        "ticker": str(row.get("ticker") or ""),
        "name": str(row.get("name") or ""),
        "page": str(pathname or ""),
    }


def quick_questions(page_ctx: dict | None) -> list[str]:
    if page_ctx:
        t = page_ctx.get("ticker") or page_ctx.get("uid")
        return [
            f"Wie steht {t} im Modell und was sind die Stärken und Schwächen?",
            f"Was sagen die letzten News und Earnings zu {t}?",
            f"Wie hat sich {t} seit dem letzten Import verändert?",
            f"Welche Peers von {t} sind aktuell attraktiver?",
        ]
    return [
        "Welche Kandidaten haben den höchsten Composite-Score?",
        "Was hat sich seit dem letzten Import im Universum verändert?",
        "Wie ist das aktive Portfolio aufgestellt und wo besteht Handlungsbedarf?",
        "Wie sind die aktuellen Zinsen, die Zinsstruktur und die Inflation in den USA?",
        "Erkläre die Zonen und die Faktoren des Modells.",
    ]


def session_title_from_question(question: str) -> str:
    return chat.session_title_from_question(question)


def _tool_block(items: list[dict]) -> html.Details | None:
    if not items:
        return None
    rows = []
    for t in items:
        args = t.get("args") or {}
        short = ", ".join(f"{k}={str(v)[:30]}" for k, v in list(args.items())[:4])
        rows.append(html.Li(f"{t.get('tool')}({short}) — {t.get('chars') or 0} Zeichen"
                            + (" (gekürzt)" if t.get("truncated") else "")))
    return html.Details(
        [html.Summary(f"Werkzeuge ({len(items)})", className="ms-chat-trace-summary"), html.Ul(rows, className="ms-chat-trace-list")],
        className="ms-chat-trace",
    )


def render_messages(messages: list[dict], job: dict | None = None, page_ctx: dict | None = None) -> list:
    """Nachrichtenliste (user/assistant/tool) als Dash-Komponenten."""
    out: list = []
    if not messages:
        out.append(html.Div("Stelle eine Frage zu Titeln, Märkten oder zum Modell. Der Agent nutzt die Koyfin-Daten, "
                            "Portfolios, Analyst-Läufe und — falls ein Key gesetzt ist — Alpha Vantage live.",
                            className="ms-chat-empty"))
        out.append(html.Div(
            [dbc.Button(q, id={"type": "ms-chat-quick", "index": i}, color="light", size="sm", className="ms-chat-quick")
             for i, q in enumerate(quick_questions(page_ctx))],
            className="ms-chat-quick-list",
        ))
    shown = messages[-MAX_RENDERED_MESSAGES:]
    if len(messages) > len(shown):
        out.append(html.Div(f"… {len(messages) - len(shown)} ältere Nachrichten ausgeblendet", className="ms-chat-note"))
    pending_tools: list[dict] = []
    for m in shown:
        role = m.get("role")
        content = str(m.get("content") or "")
        when = fmt_local_dt(m.get("created_at")) if m.get("created_at") else ""
        if role == "tool":
            pending_tools.append({"tool": m.get("tool_name"), "args": (m.get("tool_calls") or {}).get("arguments") if isinstance(m.get("tool_calls"), dict) else {},
                                  "chars": len(content), "truncated": False})
            continue
        if role == "user":
            out.append(html.Div([html.Div(content, className="ms-chat-bubble-text"), html.Div(when, className="ms-chat-meta")],
                                className="ms-chat-bubble is-user"))
            pending_tools = []
            continue
        trace = m.get("trace") if isinstance(m.get("trace"), dict) else {}
        tools = trace.get("tools") if isinstance(trace.get("tools"), list) else pending_tools
        meta_bits = [when]
        if m.get("model"):
            meta_bits.append(str(m["model"]))
        if trace.get("mode"):
            meta_bits.append(f"Modus {trace['mode']}")
        if m.get("n_llm_calls"):
            meta_bits.append(f"{m['n_llm_calls']} LLM-Calls")
        if m.get("n_av_calls"):
            meta_bits.append(f"{m['n_av_calls']} AV-Calls")
        children = [dcc.Markdown(content, className="ms-agent-modal-body ms-chat-md", link_target="_blank")]
        block = _tool_block(tools)
        if block is not None:
            children.append(block)
        children.append(html.Div(" · ".join(b for b in meta_bits if b), className="ms-chat-meta"))
        out.append(html.Div(children, className="ms-chat-bubble is-assistant" + (" is-error" if trace.get("error") else "")))
        pending_tools = []
    if job and job.get("status") == "running":
        out.append(html.Div(
            [dbc.Spinner(size="sm", color="secondary"), html.Span(job.get("progress") or "Antwort wird erstellt …", className="ms-chat-progress-text")],
            className="ms-chat-bubble is-assistant is-pending",
        ))
    return out


def _key_warnings() -> list:
    out = []
    if not os.getenv("OLLAMA_API_KEY", "").strip():
        out.append(html.Div("OLLAMA_API_KEY ist nicht gesetzt — der KI-Chat kann nicht antworten.", className="ms-agent-warnstrip ms-chat-warn"))
    if not os.getenv("ALPHAVANTAGE_API_KEY", "").strip():
        out.append(html.Div("ALPHAVANTAGE_API_KEY fehlt — nur gespeicherte Daten, keine Live-News/Kurse.", className="ms-agent-warnstrip ms-chat-warn"))
    return out


def _session_options(sessions: list[dict]) -> list[dict]:
    opts = []
    for s in sessions:
        when = fmt_local_dt(s.get("updated_at")) if s.get("updated_at") else ""
        label = f"{s.get('title') or 'Unterhaltung'} ({when})" if when else str(s.get("title") or "Unterhaltung")
        opts.append({"label": label, "value": str(s["id"])})
    return opts


def _fingerprint(session_id, messages: list[dict], job: dict | None, page_ctx: dict | None) -> str:
    last = messages[-1] if messages else {}
    return "|".join([
        str(session_id), str(len(messages)), str(last.get("id") or ""),
        str((job or {}).get("status") or ""), str((job or {}).get("progress") or ""), str((job or {}).get("seq") or ""),
        str((page_ctx or {}).get("uid") or ""),
    ])


# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #
def layout() -> html.Div:
    return html.Div(
        [
            dcc.Store(id="ms-chat-session", storage_type="session"),
            dcc.Store(id="ms-chat-ctx"),
            dcc.Store(id="ms-chat-fp"),
            dcc.Interval(id="ms-chat-poll", interval=2000, disabled=True),
            html.Button(
                [html.I(className="bi bi-chat-dots"), html.Span("KI-Chat", className="ms-chat-fab-label")],
                id="ms-chat-fab", className="ms-chat-fab", title="Mit dem KI-Analysten chatten", n_clicks=0,
            ),
            dbc.Offcanvas(
                [
                    html.Div(
                        [
                            dbc.Select(id="ms-chat-session-select", options=[], value=None, placeholder="Neue Unterhaltung", size="sm", className="ms-chat-session-select"),
                            dbc.Button(html.I(className="bi bi-plus-lg"), id="ms-chat-new", color="light", size="sm", title="Neue Unterhaltung", n_clicks=0),
                            dbc.Button(html.I(className="bi bi-trash"), id="ms-chat-delete", color="light", size="sm", title="Unterhaltung löschen", n_clicks=0),
                        ],
                        className="ms-chat-toolbar",
                    ),
                    html.Div(
                        [
                            dbc.Input(id="ms-chat-title", type="text", size="sm", placeholder="Titel der Unterhaltung", className="ms-chat-title-input"),
                            dbc.Button("Umbenennen", id="ms-chat-rename", color="light", size="sm", n_clicks=0),
                        ],
                        className="ms-chat-toolbar ms-chat-toolbar-rename",
                    ),
                    html.Div(
                        [
                            html.Div(id="ms-chat-ctx-chip", className="ms-chat-ctx-chip"),
                            dbc.Checkbox(id="ms-chat-use-ctx", value=True, label="Seitenbezug nutzen", className="ms-chat-use-ctx"),
                        ],
                        className="ms-chat-ctxbar",
                    ),
                    html.Div(_key_warnings(), id="ms-chat-warn"),
                    html.Div(id="ms-chat-messages", className="ms-chat-messages"),
                    html.Div(id="ms-chat-alert", className="ms-chat-alert"),
                    html.Div(
                        [
                            dbc.Textarea(id="ms-chat-input", placeholder="Frage eingeben … (Enter sendet, Shift+Enter = Zeilenumbruch)", rows=2, className="ms-chat-input"),
                            dbc.Button([html.I(className="bi bi-send"), " Senden"], id="ms-chat-send", color="dark", size="sm", n_clicks=0, className="ms-chat-send"),
                        ],
                        className="ms-chat-composer",
                    ),
                    html.Div("Entscheidungsunterstützung, keine Anlageberatung. Antworten basieren auf Tool-Daten und Alpha Vantage.", className="ms-chat-disclaimer"),
                ],
                id="ms-chat-panel",
                title="KI-Chat · Analyst",
                placement="end",
                is_open=False,
                backdrop=False,
                scrollable=True,
                class_name="ms-chat-panel",
            ),
        ],
        className="ms-chat-root",
    )


# --------------------------------------------------------------------------- #
# Callbacks
# --------------------------------------------------------------------------- #
@callback(
    Output("ms-chat-panel", "is_open"),
    Input("ms-chat-fab", "n_clicks"),
    State("ms-chat-panel", "is_open"),
    prevent_initial_call=True,
)
def _toggle_panel(n_clicks, is_open):
    if not n_clicks:
        raise PreventUpdate
    return not bool(is_open)


@callback(
    Output("ms-chat-ctx", "data"),
    Input("ms-location", "pathname"),
    Input("ms-location", "search"),
)
def _page_context(pathname, search):
    return parse_page_context(pathname, search, STATE.scored)


@callback(
    Output("ms-chat-ctx-chip", "children"),
    Input("ms-chat-ctx", "data"),
    Input("ms-chat-use-ctx", "value"),
)
def _ctx_chip(data, use_ctx):
    if not data:
        return html.Span("Kein Titel-Bezug auf dieser Seite", className="ms-chat-ctx-none")
    label = f"Bezug: {data.get('ticker')} — {data.get('name')}"
    return html.Span(label, className="ms-chat-ctx-on" if use_ctx else "ms-chat-ctx-off")


@callback(
    Output("ms-chat-session-select", "options"),
    Output("ms-chat-session-select", "value"),
    Output("ms-chat-session", "data"),
    Output("ms-chat-title", "value"),
    Input("ms-chat-panel", "is_open"),
    Input("ms-chat-new", "n_clicks"),
    Input("ms-chat-delete", "n_clicks"),
    Input("ms-chat-rename", "n_clicks"),
    Input("ms-chat-session-select", "value"),
    State("ms-chat-session", "data"),
    State("ms-chat-title", "value"),
)
def _sessions(is_open, n_new, n_del, n_ren, selected, stored, title):
    trig = ctx.triggered_id
    current = stored
    if trig == "ms-chat-new":
        if not n_new:
            raise PreventUpdate
        current = None
    elif trig == "ms-chat-delete":
        if not n_del or not stored:
            raise PreventUpdate
        try:
            store.delete_chat_session(int(stored))
        except Exception:  # noqa: BLE001
            pass
        current = None
    elif trig == "ms-chat-rename":
        if not n_ren or not stored or not (title or "").strip():
            raise PreventUpdate
        try:
            store.rename_chat_session(int(stored), title.strip()[:120])
        except Exception:  # noqa: BLE001
            pass
    elif trig == "ms-chat-session-select":
        current = int(selected) if selected not in (None, "") else None
    else:
        if not is_open:
            raise PreventUpdate
    sessions = store.list_chat_sessions(limit=50)
    ids = {int(s["id"]) for s in sessions}
    if current is not None and int(current) not in ids:
        current = None
    opts = _session_options(sessions)
    cur_title = next((s.get("title") for s in sessions if current is not None and int(s["id"]) == int(current)), "")
    return opts, (str(current) if current is not None else None), current, cur_title or ""


@callback(
    Output("ms-chat-input", "value"),
    Output("ms-chat-poll", "disabled", allow_duplicate=True),
    Output("ms-chat-alert", "children"),
    Output("ms-chat-session", "data", allow_duplicate=True),
    Output("ms-chat-session-select", "options", allow_duplicate=True),
    Output("ms-chat-session-select", "value", allow_duplicate=True),
    Input("ms-chat-send", "n_clicks"),
    Input({"type": "ms-chat-quick", "index": ALL}, "n_clicks"),
    State("ms-chat-input", "value"),
    State({"type": "ms-chat-quick", "index": ALL}, "children"),
    State("ms-chat-session", "data"),
    State("ms-chat-ctx", "data"),
    State("ms-chat-use-ctx", "value"),
    prevent_initial_call=True,
)
def _send(n_send, quick_clicks, text, quick_texts, session_id, page_ctx, use_ctx):
    trig = ctx.triggered_id
    triggered_value = ctx.triggered[0]["value"] if ctx.triggered else None
    if not triggered_value:
        raise PreventUpdate  # Phantom-Fire neu gerenderter Buttons
    if isinstance(trig, dict) and trig.get("type") == "ms-chat-quick":
        idx = int(trig.get("index", 0))
        question = str(quick_texts[idx]) if quick_texts and idx < len(quick_texts) else ""
    else:
        question = str(text or "")
    question = question.strip()
    if not question:
        raise PreventUpdate
    page_uid = (page_ctx or {}).get("uid") if (page_ctx and use_ctx) else None
    new_session = session_id is None
    if new_session:
        try:
            session_id = store.create_chat_session(chat.session_title_from_question(question), context_uid=page_uid)
        except Exception as exc:  # noqa: BLE001
            return no_update, no_update, html.Span(f"Sitzung konnte nicht angelegt werden: {exc}", className="text-danger"), no_update, no_update, no_update
    ok, msg = chat.start_answer(int(session_id), question, page_uid=page_uid, use_page_context=bool(use_ctx))
    if not ok:
        return no_update, no_update, html.Span(msg, className="text-danger"), (session_id if new_session else no_update), no_update, no_update
    opts = _session_options(store.list_chat_sessions(limit=50)) if new_session else no_update
    return "", False, "", (int(session_id) if new_session else no_update), opts, (str(session_id) if new_session else no_update)


@callback(
    Output("ms-chat-messages", "children"),
    Output("ms-chat-poll", "disabled"),
    Output("ms-chat-fp", "data"),
    Input("ms-chat-poll", "n_intervals"),
    Input("ms-chat-session", "data"),
    Input("ms-chat-ctx", "data"),
    State("ms-chat-fp", "data"),
)
def _poll(_n, session_id, page_ctx, fp):
    job = chat.job_status(session_id) if session_id is not None else None
    messages = store.load_chat_messages(int(session_id), limit=200) if session_id is not None else []
    new_fp = _fingerprint(session_id, messages, job, page_ctx)
    running = bool(job and job.get("status") == "running")
    if new_fp == fp:
        return no_update, (not running), no_update
    return render_messages(messages, job, page_ctx), (not running), new_fp
