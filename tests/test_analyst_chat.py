"""Tests der KI-Chat-Schleife (Fake-Ollama, frische SQLite, kein Netzwerk)."""

from __future__ import annotations

import importlib
import time
from datetime import date

import pandas as pd
import pytest

from app.core.analyst.ollama_client import OllamaError
from app.core.config import Settings


def _scored() -> pd.DataFrame:
    return pd.DataFrame({
        "uid": ["SAP", "MBG"], "ticker": ["SAP", "MBG"], "name": ["SAP SE", "Mercedes-Benz Group AG"],
        "sector": ["IT", "Consumer"], "industry": ["Software", "Autos"], "industry_group": ["Software", "Autos"],
        "region": ["EU", "EU"], "filter_pass": [True, True], "zone_v2": ["KANDIDAT", "HALTEN"],
        "composite_score": [88.0, 70.0], "composite_z": [1.1, 0.2], "composite_pct": [0.9, 0.7],
        "classification_v2": ["A", "B"], "data_coverage_v2": [0.9, 0.9],
        "z_value": [1.2, 0.1], "z_quality": [0.5, 0.2], "z_momentum": [-1.0, 0.3], "z_investment": [0.2, 0.1],
        "market_cap": [250000.0, 60000.0], "export_date": [date(2026, 10, 17)] * 2, "sma_signal": ["x", "y"],
        "total_score": [71.0, 55.0], "trend_warning": [False, False],
    })


class _State:
    def portfolio_weights(self, pid=None):
        return {"MBG": 1.0}


class FakeOllama:
    """Erste Runde mit Werkzeugen → ein Tool-Call; danach Abschluss."""

    def __init__(self, unsupported=False, always_call=False, fail=None):
        self.unsupported = unsupported
        self.always_call = always_call
        self.fail = fail
        self.calls: list[dict] = []

    @staticmethod
    def api_key():
        return "k"

    @staticmethod
    def tools_unsupported(exc):
        return "tool" in str(exc).lower() and "support" in str(exc).lower()

    def chat_tools(self, messages, model, tools=None, temperature=0.2, timeout=300):
        self.calls.append({"messages": list(messages), "model": model, "tools": tools, "timeout": timeout})
        if self.fail:
            raise self.fail
        if self.unsupported and tools:
            raise OllamaError("Ollama: HTTP 400 registry.ollama.ai/library/x does not support tools", kind="error")
        has_tool_result = any(m.get("role") == "tool" for m in messages)
        if tools and (self.always_call or not has_tool_result):
            return {"content": "", "thinking": None,
                    "tool_calls": [{"name": "company_snapshot", "arguments": '{"uid": "SAP"}', "id": "c1"}],
                    "raw_tool_calls": [{"function": {"name": "company_snapshot", "arguments": {"uid": "SAP"}}}]}
        n_tools = sum(1 for m in messages if m.get("role") == "tool")
        return {"content": f"Antwort mit {n_tools} Werkzeug-Ergebnissen.", "tool_calls": [], "raw_tool_calls": [], "thinking": None}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/chat.db")
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    from app.core import persistence
    from app.core.analyst import chat, store

    importlib.reload(persistence)
    importlib.reload(store)
    chat._JOBS.clear()
    return chat, store


def test_tool_loop_persists_and_appends_sources(env):
    chat, store = env
    fake = FakeOllama()
    chat.ollama = fake
    s = Settings()
    sid = store.create_chat_session("t")
    ctx = chat.build_context(s, _scored(), _State(), page_uid="MBG")
    res = chat.answer(sid, "Wie steht SAP?", ctx, s)
    assert res.error is None and res.mode == "tools" and res.n_llm_calls == 2
    assert [t["tool"] for t in res.tool_trace] == ["company_snapshot"]
    assert res.answer_md.startswith("Antwort mit 1 Werkzeug-Ergebnissen.")
    assert "**Quellen:**" in res.answer_md and "Koyfin-Import" in res.answer_md
    roles = [m["role"] for m in store.load_chat_messages(sid)]
    assert roles == ["user", "tool", "assistant"]
    # Argumente als JSON-String wurden geparst und an das Werkzeug gereicht
    assert res.tool_trace[0]["args"] == {"uid": "SAP"}
    # Tool-Spezifikationen ohne Live-Tools (kein AV-Key), Systemprompt mit Datum und Seitenbezug
    first = fake.calls[0]
    assert all(t["function"]["name"] != "av_news" for t in first["tools"])
    assert "2026" in first["messages"][0]["content"] or str(date.today().year) in first["messages"][0]["content"]
    assert "Seitenbezug: MBG" in first["messages"][0]["content"]
    assert first["messages"][-1] == {"role": "user", "content": "Wie steht SAP?"}
    # Zweite Frage bekommt den Verlauf (ohne Quellen-Footer) als Kontext
    res2 = chat.answer(sid, "Und weiter?", chat.build_context(s, _scored(), _State()), s)
    hist = fake.calls[-1]["messages"]
    assert hist[1]["role"] == "user" and hist[2]["role"] == "assistant" and "Quellen" not in hist[2]["content"]
    assert res2.n_llm_calls == 2
    sess = store.load_chat_session(sid)
    assert sess["n_messages"] == 6


def test_max_rounds_forces_summary(env):
    chat, store = env
    fake = FakeOllama(always_call=True)
    chat.ollama = fake
    s = Settings()
    s.analyst_chat_max_tool_rounds = 2
    sid = store.create_chat_session("t")
    res = chat.answer(sid, "Frage", chat.build_context(s, _scored(), _State()), s)
    assert res.n_llm_calls == 3 and len(res.tool_trace) == 2
    last = fake.calls[-1]
    assert last["tools"] is None and "Werkzeug-Limit" in last["messages"][-1]["content"]
    assert res.answer_md.startswith("Antwort mit 2 Werkzeug-Ergebnissen.")


def test_auto_mode_falls_back_to_context(env):
    chat, store = env
    fake = FakeOllama(unsupported=True)
    chat.ollama = fake
    s = Settings()
    sid = store.create_chat_session("t")
    ctx = chat.build_context(s, _scored(), _State(), page_uid="SAP")
    res = chat.answer(sid, "Wie steht Mercedes im Modell?", ctx, s)
    assert res.error is None and res.mode == "context" and res.n_llm_calls == 1
    assert len(fake.calls) == 2 and fake.calls[1]["tools"] is None
    user = fake.calls[1]["messages"][-1]["content"]
    assert "# Kontextdaten" in user and "## Titel SAP" in user and "## Titel MBG" in user and "## Methodik" in user
    assert "keine Werkzeuge" in fake.calls[1]["messages"][0]["content"]
    tools = [t["tool"] for t in res.tool_trace]
    assert tools.count("company_snapshot") == 2 and "universe_overview" in tools
    trace = store.load_chat_messages(sid, roles=("assistant",))[-1]["trace"]
    assert trace["mode"] == "context"


def test_tools_mode_passes_error_through(env):
    chat, store = env
    chat.ollama = FakeOllama(unsupported=True)
    s = Settings()
    s.analyst_chat_mode = "tools"
    sid = store.create_chat_session("t")
    res = chat.answer(sid, "Frage", chat.build_context(s, _scored(), _State()), s)
    assert res.error and "does not support tools" in res.error
    assert res.answer_md.startswith("**Fehler:**")
    msgs = store.load_chat_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "assistant"] and msgs[-1]["trace"]["error"]


def test_context_mode_setting_and_model_resolution(env):
    chat, store = env
    fake = FakeOllama()
    chat.ollama = fake
    s = Settings()
    s.analyst_chat_mode = "context"
    s.analyst_model = "modell-a"
    assert chat.resolve_model(s) == "modell-a"
    s.analyst_chat_model = "modell-chat"
    assert chat.resolve_model(s) == "modell-chat" and chat.resolve_model(s, "x") == "x"
    assert chat.resolve_mode(s) == "context" and chat.resolve_mode(s, "weird") == "auto"
    sid = store.create_chat_session("t")
    res = chat.answer(sid, "Erkläre die Zonen.", chat.build_context(s, _scored(), _State()), s)
    assert res.mode == "context" and fake.calls[0]["tools"] is None and fake.calls[0]["model"] == "modell-chat"


def test_missing_key_and_empty_question(env, monkeypatch):
    chat, store = env
    chat.ollama = FakeOllama()
    s = Settings()
    sid = store.create_chat_session("t")
    with pytest.raises(ValueError):
        chat.answer(sid, "   ", chat.build_context(s, _scored(), _State()), s)
    monkeypatch.setattr(chat.ollama, "api_key", staticmethod(lambda: None))
    with pytest.raises(chat.ChatConfigError):
        chat.answer(sid, "Frage", chat.build_context(s, _scored(), _State()), s)
    ok, msg = chat.start_answer(sid, "Frage", settings=s, scored=_scored(), state=_State())
    assert ok is False and "OLLAMA_API_KEY" in msg


def test_detect_companies_and_helpers(env):
    chat, store = env
    ctx = chat.build_context(Settings(), _scored(), _State())
    assert chat.detect_companies(ctx, "Wie steht SAP gegen Mercedes?") == ["SAP", "MBG"]
    assert chat.detect_companies(ctx, "Was ist die Zone?") == []
    assert chat.session_title_from_question("  Wie   steht SAP? ") == "Wie steht SAP?"
    assert chat.session_title_from_question("x" * 80).endswith("…") and len(chat.session_title_from_question("x" * 80)) == 60
    assert chat.strip_sources("Text\n\n**Quellen:**\n- a") == "Text"
    assert chat.append_sources("Text", ["a"]) == "Text\n\n**Quellen:**\n- a"
    assert chat.append_sources("Text\n\n**Quellen:**\n- a", ["b"]).count("Quellen") == 1


def test_job_registry_runs_in_background(env):
    chat, store = env
    chat.ollama = FakeOllama()
    s = Settings()
    sid = store.create_chat_session("t")
    ok, msg = chat.start_answer(sid, "Frage", settings=s, scored=_scored(), state=_State(), page_uid="SAP")
    assert ok, msg
    for _ in range(100):
        if chat.job_status(sid)["status"] != "running":
            break
        time.sleep(0.05)
    job = chat.job_status(sid)
    assert job["status"] == "done" and job["n_llm_calls"] == 2 and job["error"] is None
    assert [m["role"] for m in store.load_chat_messages(sid)] == ["user", "tool", "assistant"]
    assert chat.job_status(None) is None and chat.job_status(999) is None
    assert chat.start_answer(sid, "", settings=s, scored=_scored(), state=_State()) == (False, "Bitte eine Frage eingeben.")
    ok, msg = chat.start_answer(sid, "x", settings=s, scored=pd.DataFrame(), state=_State())
    assert not ok and "Kein Universum" in msg


def test_job_registry_guards(env):
    chat, store = env
    chat.ollama = FakeOllama()
    s = Settings()
    sid = store.create_chat_session("t")
    chat._JOBS[sid] = {"status": "running", "seq": 1}
    ok, msg = chat.start_answer(sid, "x", settings=s, scored=_scored(), state=_State())
    assert not ok and "läuft bereits" in msg
    chat._JOBS.clear()
    chat._JOBS[100] = {"status": "running"}
    chat._JOBS[101] = {"status": "running"}
    ok, msg = chat.start_answer(sid, "x", settings=s, scored=_scored(), state=_State())
    assert not ok and "zu viele" in msg
    chat._JOBS.clear()


def test_job_exception_is_persisted(env, monkeypatch):
    chat, store = env
    chat.ollama = FakeOllama()
    s = Settings()
    sid = store.create_chat_session("t")

    def boom(*a, **k):
        raise RuntimeError("thread kaputt")

    monkeypatch.setattr(chat, "answer", boom)
    ok, _ = chat.start_answer(sid, "Frage", settings=s, scored=_scored(), state=_State())
    assert ok
    for _ in range(100):
        if chat.job_status(sid)["status"] != "running":
            break
        time.sleep(0.05)
    assert chat.job_status(sid)["status"] == "error" and "thread kaputt" in chat.job_status(sid)["error"]
    assert store.load_chat_messages(sid)[-1]["content"].startswith("**Fehler:**")
