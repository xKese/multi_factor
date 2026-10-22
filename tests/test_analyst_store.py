"""Round-Trip-Tests für die Persistenz des KI-Analysten (frische SQLite)."""

from __future__ import annotations

import importlib
from datetime import date, datetime

import pytest


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/analyst.db")
    from app.core import persistence
    from app.core.analyst import store as st

    importlib.reload(persistence)
    importlib.reload(st)
    st.ensure_tables()
    return st


def test_runs_roundtrip(store):
    store.save_run(
        {
            "run_id": "20261021_100000",
            "trigger": "cli",
            "scope": "portfolio",
            "model": "gpt-oss:120b",
            "status": "running",
            "dry_run": False,
            "started_at": datetime(2026, 10, 21, 10, 0, 0),
        }
    )
    store.update_run(
        "20261021_100000",
        status="done",
        n_llm_calls=5,
        finished_at=datetime(2026, 10, 21, 10, 5, 0),
        diagnostics=[{"severity": "Info", "code": "x", "message": "m", "uid": None}],
        unknown_column="ignored",
    )
    run = store.load_run("20261021_100000")
    assert run["status"] == "done"
    assert run["n_llm_calls"] == 5
    assert run["finished_at"] == datetime(2026, 10, 21, 10, 5, 0)
    assert run["dry_run"] is False
    assert '"code": "x"' in run["diagnostics_json"]

    store.save_run({"run_id": "20261022_100000", "status": "running", "dry_run": True})
    runs = store.list_runs(limit=5)
    assert [r["run_id"] for r in runs] == ["20261022_100000", "20261021_100000"]
    assert runs[0]["dry_run"] is True
    assert store.load_run("nope") is None


def test_events_dedupe(store):
    ev = {
        "kind": "earnings",
        "uid": "SAP",
        "ticker": "SAP",
        "period": "2026-07-28",
        "event_date": date(2026, 7, 28),
        "title": "Quartalszahlen",
        "payload": {"surprise_pct": 4.5},
        "dedupe_key": "SAP|earnings|2026-07-28",
    }
    ev2 = {**ev, "kind": "news", "period": "2026-10-20T14:30:00", "dedupe_key": "SAP|news|x"}
    assert store.save_events([ev, ev2], run_id="r1") == 2
    assert store.save_events([ev, ev2], run_id="r2") == 0
    assert store.save_events([{**ev, "dedupe_key": "SAP|earnings|new"}], run_id="r2") == 1

    events = store.list_events(limit=10)
    assert len(events) == 3
    assert events[-1]["payload"] == {"surprise_pct": 4.5}
    assert events[-1]["event_date"] == date(2026, 7, 28)
    assert [e["kind"] for e in store.list_events(uid="SAP", kind="news")] == ["news"]
    assert store.save_events([]) == 0


def test_insights_upsert_and_lookup(store):
    rec = {
        "run_id": "r1",
        "uid": "SAP",
        "ticker": "SAP",
        "kind": "transcript_summary",
        "period": "2026Q2",
        "status": "ok",
        "model": "m",
        "content": {"zusammenfassung": "A"},
        "raw_text": "{}",
        "prompt_hash": "abc",
        "n_calls": 1,
    }
    store.save_insight(rec)
    store.save_insight({**rec, "content": {"zusammenfassung": "B"}})
    rows = store.list_insights(uid="SAP")
    assert len(rows) == 1
    assert rows[0]["content"] == {"zusammenfassung": "B"}
    assert store.has_insight("SAP", "transcript_summary", "2026Q2") is True
    assert store.has_insight("SAP", "transcript_summary", "2026Q3") is False
    # unparsed zählt nicht als vorhanden
    store.save_insight({**rec, "period": "2026Q3", "status": "unparsed", "content": None})
    assert store.has_insight("SAP", "transcript_summary", "2026Q3") is False
    assert store.load_insight("SAP", "transcript_summary", "2026Q2")["status"] == "ok"
    assert store.load_insight("SAP", "transcript_summary")["period"] == "2026Q3"

    assert store.load_latest_synthesis("SAP") is None
    store.save_insight({**rec, "kind": "synthesis", "period": "2026-10-21", "content": {"vorschlag": {"typ": "INFO"}}})
    store.save_insight({**rec, "kind": "synthesis", "period": "2026-10-22", "status": "unparsed", "content": None})
    latest = store.load_latest_synthesis("SAP")
    assert latest["period"] == "2026-10-21"

    # Run-Ebene (uid None)
    store.save_insight({"run_id": "r1", "kind": "trend", "period": "r1", "status": "ok", "content": {"trends": []}})
    assert store.list_insights(kind="trend")[0]["uid"] is None


def test_suggestions_latest_per_uid(store):
    base = {
        "run_id": "r1", "uid": "SAP", "ticker": "SAP", "suggestion_type": "ÜBERPRÜFEN",
        "confidence": 0.6, "rationale": "Grund", "evidence": [{"quelle": "news", "referenz": "[1]"}],
        "quant_zone": "HALTEN", "quant_score": 72.0, "quant_consistency": "neutral",
        "n_interplay": 2, "n_unpriced": 1,
    }
    id1 = store.save_suggestion(base)
    id2 = store.save_suggestion({**base, "run_id": "r2", "suggestion_type": "EINSTIEG_PRÜFEN"})
    id3 = store.save_suggestion({**base, "uid": "MBG", "ticker": "MBG"})
    assert id1 < id2 < id3

    latest = store.load_latest_suggestions()
    assert set(latest["uid"]) == {"SAP", "MBG"}
    assert latest.set_index("uid").loc["SAP", "suggestion_type"] == "EINSTIEG_PRÜFEN"

    store.set_suggestion_status(id2, "done")
    latest_open = store.load_latest_suggestions()
    assert set(latest_open["uid"]) == {"MBG"}
    assert set(store.load_latest_suggestions(open_only=False)["uid"]) == {"SAP", "MBG"}

    rows = store.list_suggestions(uid="SAP")
    assert [r["id"] for r in rows] == [id2, id1]
    assert rows[0]["status"] == "done" and rows[0]["resolved_at"] is not None
    assert rows[0]["evidence"][0]["quelle"] == "news"
    assert store.list_suggestions(run_id="r2", status="done")[0]["id"] == id2
    with pytest.raises(ValueError):
        store.set_suggestion_status(id1, "weird")


def test_themes_and_company_links(store):
    assert store.list_themes() == []
    store.save_themes(
        "r1",
        [
            {"title": "KI-Capex", "description": "d", "kind": "sektor", "direction": "positiv",
             "confidence": 0.7, "uids": ["SAP", "MBG"], "sectors": ["IT"], "regions": ["EU"],
             "evidence": ["x"], "confirmation": "Q3-Zahlen"},
        ],
    )
    store.save_themes("r2", [{"title": "Zölle", "uids": ["MBG"]}])
    themes = store.list_themes()
    assert [t["title"] for t in themes] == ["Zölle"]
    assert store.list_themes(run_id="r1")[0]["uids"] == ["SAP", "MBG"]
    assert store.save_themes("r3", []) == 0

    store.save_company_links(
        "r1",
        [
            {"uid": "SAP", "ticker": "SAP", "related_uid": "MBG", "related_name": "Mercedes",
             "related_ticker": "MBG", "relation": "kunde", "context": "Großkunde", "source": "transcript"},
            {"uid": "SAP", "related_name": "Unbekannt AG", "relation": "wettbewerber", "source": "news"},
            {"uid": "", "related_name": "x"},
        ],
    )
    out = store.load_company_links_for("SAP")
    assert len(out) == 2 and all(l["direction"] == "out" for l in out)
    inbound = store.load_company_links_for("MBG")
    assert len(inbound) == 1 and inbound[0]["direction"] == "in"
    assert inbound[0]["related_name"] == "Mercedes"
    assert len(store.list_company_links()) == 2


def test_trends_and_links(store):
    assert store.list_trends() == []
    assert store.load_latest_trend_links().empty
    trends = [
        {"title": "Netzausbau", "description": "d", "kind": "energie_transport", "horizon": "mittel",
         "strength": 4, "confidence": 0.8, "evidence": [{"quelle": "news", "referenz": "[3]"}],
         "industry_groups": ["Capital Goods"], "loser_note": None},
        {"title": "Zweiter Trend", "kind": "makro"},
    ]
    links = [
        {"trend_idx": 0, "uid": "SIE", "ticker": "SIE", "role": "direkt", "pricing_llm": "frueh",
         "pricing_quant": "früh", "action": "Kandidat prüfen", "reasoning": ["a", "b"], "evidence": ["[3]"]},
        {"trend_idx": 1, "uid": "SAP", "ticker": "SAP", "role": "enabler", "pricing_quant": "bereits_eingepreist"},
        {"trend_idx": 9, "uid": "X"},
    ]
    assert store.save_trends("r1", trends, links) == (2, 2)
    loaded = store.list_trends()
    assert [t["title"] for t in loaded] == ["Netzausbau", "Zweiter Trend"]
    assert loaded[0]["links"][0]["reasoning"] == ["a", "b"]
    assert loaded[0]["industry_groups"] == ["Capital Goods"]

    store.save_trends("r2", [{"title": "Neu"}], [{"trend_idx": 0, "uid": "SIE", "pricing_quant": "teilweise_eingepreist"}])
    df = store.load_latest_trend_links()
    assert list(df["uid"]) == ["SIE"]
    assert df.iloc[0]["trend_title"] == "Neu"

    for_sie = store.load_trend_links_for("SIE")
    assert [l["trend_title"] for l in for_sie] == ["Neu", "Netzausbau"]
    assert store.save_trends("r3", [], []) == (0, 0)


def test_watch_state_partial_update(store):
    assert store.load_watch_state("SAP") is None
    store.save_watch_state(
        "SAP",
        av_symbol="SAP.DEX",
        last_earnings_report_date=date(2026, 7, 28),
        last_news_at=datetime(2026, 10, 20, 14, 30),
        last_zone="KANDIDAT",
        last_score=81.5,
        ignored="x",
    )
    store.save_watch_state("SAP", last_transcript_quarter="2026Q2", last_checked_at=datetime(2026, 10, 21))
    st = store.load_watch_state("SAP")
    assert st["av_symbol"] == "SAP.DEX"
    assert st["last_earnings_report_date"] == date(2026, 7, 28)
    assert st["last_news_at"] == datetime(2026, 10, 20, 14, 30)
    assert st["last_transcript_quarter"] == "2026Q2"
    assert st["last_score"] == pytest.approx(81.5)
    assert st["last_checked_at"] == datetime(2026, 10, 21)
    assert "ignored" not in st
    many = store.load_watch_states(["SAP", "MBG"])
    assert set(many) == {"SAP"}
    assert store.load_watch_states([]) == {}


def test_reads_fail_open(store, monkeypatch):
    from app.core import persistence

    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert store.list_runs() == []
    assert store.load_run("x") is None
    assert store.list_events() == []
    assert store.has_insight("a", "b", "c") is False
    assert store.load_insight("a", "b") is None
    assert store.list_insights() == []
    assert store.load_latest_synthesis("a") is None
    assert store.list_suggestions() == []
    assert store.load_latest_suggestions().empty
    assert store.list_themes() == []
    assert store.load_company_links_for("a") == []
    assert store.list_trends() == []
    assert store.load_latest_trend_links().empty
    assert store.load_trend_links_for("a") == []
    assert store.load_watch_states(["a"]) == {}
    with pytest.raises(RuntimeError):
        store.save_run({"run_id": "x"})


def test_chat_sessions_and_messages(store):
    sid = store.create_chat_session("Erste Frage", context_uid="SAP")
    sid2 = store.create_chat_session()
    assert sid2 == sid + 1
    sessions = store.list_chat_sessions()
    assert [s["id"] for s in sessions] == [sid2, sid]
    assert sessions[1]["title"] == "Erste Frage" and sessions[1]["context_uid"] == "SAP"
    assert sessions[0]["title"] == "Neue Unterhaltung" and sessions[0]["n_messages"] == 0

    m1 = store.append_chat_message(sid, "user", "Wie steht SAP?")
    m2 = store.append_chat_message(sid, "tool", "{…}", tool_name="company_snapshot", tool_calls={"uid": "SAP"})
    m3 = store.append_chat_message(sid, "assistant", "## SAP", model="m", n_llm_calls=2, n_av_calls=1,
                                   trace=[{"name": "company_snapshot", "args": {"uid": "SAP"}, "chars": 3}])
    assert m1 < m2 < m3
    sess = store.load_chat_session(sid)
    assert sess["n_messages"] == 3
    # Aktualisierte Sitzung rutscht nach oben.
    assert store.list_chat_sessions()[0]["id"] == sid

    msgs = store.load_chat_messages(sid)
    assert [m["role"] for m in msgs] == ["user", "tool", "assistant"]
    assert msgs[1]["tool_name"] == "company_snapshot" and msgs[1]["tool_calls"] == {"uid": "SAP"}
    assert msgs[2]["trace"][0]["name"] == "company_snapshot" and msgs[2]["n_llm_calls"] == 2
    assert [m["role"] for m in store.load_chat_messages(sid, roles=("user", "assistant"))] == ["user", "assistant"]
    assert [m["id"] for m in store.load_chat_messages(sid, limit=1)] == [m3]

    store.rename_chat_session(sid, "Umbenannt")
    assert store.load_chat_session(sid)["title"] == "Umbenannt"
    store.touch_chat_session(sid2)
    assert store.list_chat_sessions()[0]["id"] == sid2
    store.delete_chat_session(sid)
    assert store.load_chat_session(sid) is None and store.load_chat_messages(sid) == []
    with pytest.raises(ValueError):
        store.append_chat_message(sid2, "system", "x")


def test_chat_reads_fail_open(store, monkeypatch):
    from app.core import persistence

    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert store.list_chat_sessions() == [] and store.load_chat_session(1) is None
    assert store.load_chat_messages(1) == []
