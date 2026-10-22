"""Smoke-Tests des KI-Chat-Popups (Layout im Root, reine Helfer, Callbacks)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest
from dash.exceptions import PreventUpdate

from app.main import create_app

APP = create_app()

from app.core.state import STATE  # noqa: E402
from app.ui import chat_panel as cp  # noqa: E402


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "children") or hasattr(child, "_prop_names"):
            yield from _walk(child)


def _scored() -> pd.DataFrame:
    return pd.DataFrame({
        "uid": ["SAP", "SAN~sanofi"], "ticker": ["SAP", "SAN"], "name": ["SAP SE", "Sanofi"],
        "zone_v2": ["KANDIDAT", "HALTEN"], "composite_score": [88.0, 70.0],
    })


def test_panel_ids_mounted_in_root_layout():
    app = APP  # nicht dash.get_app(): andere Tests legen eigene Dash-Instanzen an
    ids = {c.id for c in _walk(app.layout) if isinstance(getattr(c, "id", None), str)}
    for cid in ("ms-chat-fab", "ms-chat-panel", "ms-chat-session", "ms-chat-ctx", "ms-chat-fp", "ms-chat-poll",
                "ms-chat-session-select", "ms-chat-new", "ms-chat-delete", "ms-chat-rename", "ms-chat-title",
                "ms-chat-ctx-chip", "ms-chat-use-ctx", "ms-chat-messages", "ms-chat-input", "ms-chat-send", "ms-chat-alert"):
        assert cid in ids, cid
    poll = next(c for c in _walk(app.layout) if getattr(c, "id", None) == "ms-chat-poll")
    assert poll.disabled is True and poll.interval == 2000


def test_parse_page_context_and_quick_questions():
    df = _scored()
    assert cp.parse_page_context("/einzelanalyse", "?ticker=SAP", df) == {"uid": "SAP", "ticker": "SAP", "name": "SAP SE", "page": "/einzelanalyse"}
    assert cp.parse_page_context("/agenten-analyse", "?uid=SAN~sanofi", df)["name"] == "Sanofi"
    assert cp.parse_page_context("/", "", df) is None
    assert cp.parse_page_context("/einzelanalyse", "?ticker=NOPE", df) is None
    assert cp.parse_page_context("/einzelanalyse", "?ticker=SAP", pd.DataFrame()) is None
    assert any("SAP" in q for q in cp.quick_questions({"ticker": "SAP", "uid": "SAP"}))
    assert len(cp.quick_questions(None)) == 5
    assert cp.session_title_from_question("Wie steht SAP?") == "Wie steht SAP?"


def test_render_messages_roles_trace_and_pending():
    msgs = [
        {"id": 1, "role": "user", "content": "Frage", "created_at": datetime(2026, 10, 22, 10, 0)},
        {"id": 2, "role": "tool", "content": "Werkzeugtext", "tool_name": "company_snapshot", "tool_calls": {"arguments": {"uid": "SAP"}}},
        {"id": 3, "role": "assistant", "content": "**Antwort**", "model": "m", "n_llm_calls": 2, "n_av_calls": 1,
         "trace": {"mode": "tools", "tools": [{"tool": "company_snapshot", "args": {"uid": "SAP"}, "chars": 12, "truncated": True}]}},
    ]
    out = cp.render_messages(msgs, {"status": "running", "progress": "Werkzeug av_news(uid=SAP) …"}, None)
    text = str(out)
    assert "is-user" in text and "is-assistant" in text and "is-pending" in text
    assert "Werkzeuge (1)" in text and "company_snapshot(uid=SAP) — 12 Zeichen (gekürzt)" in text
    assert "Werkzeug av_news(uid=SAP)" in text and "2 LLM-Calls" in text and "1 AV-Calls" in text
    assert "ms-chat-quick" not in text
    empty = str(cp.render_messages([], None, {"ticker": "SAP", "uid": "SAP"}))
    assert "ms-chat-quick" in empty and "SAP" in empty
    # Tool-Nachrichten ohne Trace im Assistant → aus den vorangegangenen Tool-Zeilen
    out2 = str(cp.render_messages(msgs[:2] + [{"id": 4, "role": "assistant", "content": "x", "trace": {}}], None, None))
    assert "Werkzeuge (1)" in out2
    many = [{"id": i, "role": "user", "content": f"m{i}"} for i in range(70)]
    assert "ältere Nachrichten ausgeblendet" in str(cp.render_messages(many, None, None))


def test_callbacks_toggle_context_and_sessions(monkeypatch):
    monkeypatch.setattr(STATE, "scored", _scored(), raising=False)
    assert cp._toggle_panel(1, False) is True and cp._toggle_panel(2, True) is False
    with pytest.raises(PreventUpdate):
        cp._toggle_panel(0, False)
    assert cp._page_context("/einzelanalyse", "?ticker=SAP")["uid"] == "SAP"
    assert "Bezug: SAP" in str(cp._ctx_chip({"ticker": "SAP", "name": "SAP SE"}, True))
    assert "ms-chat-ctx-off" in str(cp._ctx_chip({"ticker": "SAP", "name": "SAP SE"}, False))
    assert "Kein Titel-Bezug" in str(cp._ctx_chip(None, True))


def test_send_and_poll_with_patched_store(monkeypatch):
    from app.core.analyst import chat, store

    sessions = [{"id": 7, "title": "Alt", "updated_at": datetime(2026, 10, 22, 9, 0), "n_messages": 2}]
    messages = {7: [{"id": 1, "role": "user", "content": "alt"}, {"id": 2, "role": "assistant", "content": "ok", "trace": {}}]}
    created = {}
    monkeypatch.setattr(store, "list_chat_sessions", lambda limit=50: sessions)
    monkeypatch.setattr(store, "load_chat_messages", lambda sid, limit=200, roles=None: messages.get(int(sid), []))
    monkeypatch.setattr(store, "create_chat_session", lambda title=None, context_uid=None: created.setdefault("id", 8) or 8)
    monkeypatch.setattr(store, "delete_chat_session", lambda sid: sessions.clear())
    monkeypatch.setattr(store, "rename_chat_session", lambda sid, title: sessions[0].__setitem__("title", title))
    started = {}

    def fake_start(session_id, question, *, page_uid=None, use_page_context=True, **kw):
        started.update(session_id=session_id, question=question, page_uid=page_uid)
        return True, "ok"

    monkeypatch.setattr(chat, "start_answer", fake_start)
    monkeypatch.setattr(chat, "job_status", lambda sid: {"status": "running", "progress": "p", "seq": 1} if started.get("session_id") == sid else None)

    # Sitzungen: Öffnen lädt Liste und hält gespeicherte Sitzung
    import dash

    class _Ctx:
        triggered_id = None

    monkeypatch.setattr(cp, "ctx", _Ctx)
    opts, value, stored, title = cp._sessions(True, 0, 0, 0, None, 7, "")
    assert value == "7" and stored == 7 and title == "Alt" and opts[0]["value"] == "7"
    _Ctx.triggered_id = "ms-chat-new"
    assert cp._sessions(True, 1, 0, 0, "7", 7, "")[1] is None
    _Ctx.triggered_id = "ms-chat-rename"
    cp._sessions(True, 1, 0, 1, "7", 7, "Neu")
    assert sessions[0]["title"] == "Neu"
    with pytest.raises(PreventUpdate):
        cp._sessions(True, 0, 0, 1, "7", 7, "   ")
    _Ctx.triggered_id = "ms-chat-session-select"
    assert cp._sessions(True, 0, 0, 0, "7", None, "")[2] == 7
    _Ctx.triggered_id = None
    with pytest.raises(PreventUpdate):
        cp._sessions(False, 0, 0, 0, None, None, "")

    # Senden ohne Sitzung legt eine an und startet den Job
    _Ctx.triggered_id = "ms-chat-send"
    _Ctx.triggered = [{"prop_id": "ms-chat-send.n_clicks", "value": 1}]
    out = cp._send(1, [], "Wie steht SAP?", [], None, {"uid": "SAP"}, True)
    assert out[0] == "" and out[1] is False and out[3] == 8 and out[5] == "8"
    assert started == {"session_id": 8, "question": "Wie steht SAP?", "page_uid": "SAP"}
    # Schnellfrage per Pattern-Matching-ID
    _Ctx.triggered_id = {"type": "ms-chat-quick", "index": 1}
    _Ctx.triggered = [{"prop_id": '{"index":1,"type":"ms-chat-quick"}.n_clicks', "value": 1}]
    out = cp._send(0, [None, 1], "", ["A", "B"], 7, None, False)
    assert started["question"] == "B" and started["page_uid"] is None and out[3] is dash.no_update
    # Phantom-Fire (n_clicks None) und leere Eingabe
    _Ctx.triggered = [{"prop_id": "ms-chat-send.n_clicks", "value": None}]
    with pytest.raises(PreventUpdate):
        cp._send(None, [], "x", [], 7, None, True)
    _Ctx.triggered_id = "ms-chat-send"
    _Ctx.triggered = [{"prop_id": "ms-chat-send.n_clicks", "value": 2}]
    with pytest.raises(PreventUpdate):
        cp._send(2, [], "   ", [], 7, None, True)
    # start_answer lehnt ab → Meldung, Poll bleibt aus
    monkeypatch.setattr(chat, "start_answer", lambda *a, **k: (False, "läuft bereits"))
    out = cp._send(3, [], "x", [], 7, None, True)
    assert "läuft bereits" in str(out[2]) and out[1] is dash.no_update

    # Poll: Fingerprint verhindert Re-Render, laufender Job hält den Interval aktiv
    started["session_id"] = 8  # nur Sitzung 8 läuft
    children, disabled, fp = cp._poll(0, 7, None, None)
    assert isinstance(children, list) and fp and disabled is True
    assert cp._poll(1, 7, None, fp) == (dash.no_update, True, dash.no_update)
    children, disabled, fp2 = cp._poll(2, 8, None, fp)
    assert disabled is False and fp2 != fp and "is-pending" in str(children)
    children, disabled, fp3 = cp._poll(3, None, None, fp2)
    assert disabled is True and "ms-chat-quick" in str(children)
