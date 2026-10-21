"""Tests des In-App-Schedulers (Thread-Steuerung ohne echte Läufe)."""

from __future__ import annotations

import threading
import time

import pandas as pd
import pytest

from app.core.analyst import scheduler as sch
from app.core.state import STATE


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("KI_ANALYST_SCHEDULER", raising=False)
    monkeypatch.setattr(sch, "_MIN_INTERVAL_SECONDS", 0.02)
    sch.stop(timeout=1)
    yield
    sch.stop(timeout=1)


def _install_fake_execute(monkeypatch):
    calls: list[str] = []
    done = threading.Event()

    def fake(trigger: str) -> None:
        calls.append(trigger)
        done.set()

    monkeypatch.setattr(sch, "_execute", fake)
    return calls, done


def test_start_is_idempotent_and_honours_env(monkeypatch):
    monkeypatch.setattr(STATE.settings, "analyst_enabled", False)
    assert sch.start() is True
    first = sch._thread
    assert sch.start() is True and sch._thread is first
    assert sch.get_status()["thread_alive"] is True
    sch.stop(timeout=1)
    assert sch.get_status()["thread_alive"] is False
    monkeypatch.setenv("KI_ANALYST_SCHEDULER", "0")
    assert sch.start() is False


def test_trigger_now_executes_once(monkeypatch):
    calls, done = _install_fake_execute(monkeypatch)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "a")
    monkeypatch.setenv("OLLAMA_API_KEY", "b")
    monkeypatch.setattr(STATE, "scored", pd.DataFrame({"uid": ["X"]}), raising=False)
    monkeypatch.setattr(STATE.settings, "analyst_enabled", False)
    monkeypatch.setattr(STATE.settings, "analyst_interval_minutes", 1000)
    ok, msg = sch.trigger_now()
    assert ok, msg
    assert done.wait(2)
    time.sleep(0.05)
    assert calls == ["manual"]


def test_trigger_now_guards(monkeypatch):
    monkeypatch.setattr(STATE, "scored", pd.DataFrame(), raising=False)
    assert sch.trigger_now() == (False, "Kein Universum geladen.")
    monkeypatch.setattr(STATE, "scored", pd.DataFrame({"uid": ["X"]}), raising=False)
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    assert sch.trigger_now()[1].startswith("ALPHAVANTAGE_API_KEY")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "a")
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert sch.trigger_now()[1].startswith("OLLAMA_API_KEY")
    monkeypatch.setenv("OLLAMA_API_KEY", "b")
    assert sch._RUN_LOCK.acquire(blocking=False)
    try:
        assert sch.trigger_now() == (False, "Ein Lauf ist bereits aktiv.")
        assert sch.get_status()["running"] is True
    finally:
        sch._RUN_LOCK.release()


def test_disabled_scheduler_never_fires(monkeypatch):
    calls, done = _install_fake_execute(monkeypatch)
    monkeypatch.setattr(STATE.settings, "analyst_enabled", False)
    monkeypatch.setattr(STATE.settings, "analyst_interval_minutes", 0)
    sch.start()
    assert not done.wait(0.2)
    assert calls == []
    assert sch.get_status()["next_run_at"] is None


def test_enabled_scheduler_fires_on_tick(monkeypatch):
    calls, done = _install_fake_execute(monkeypatch)
    monkeypatch.setattr(STATE.settings, "analyst_enabled", True)
    monkeypatch.setattr(STATE.settings, "analyst_interval_minutes", 0)
    sch.start()
    assert done.wait(2)
    assert calls[0] == "scheduler"
    assert sch.get_status()["next_run_at"] is not None
    sch.notify_settings_changed()


def test_execute_records_errors(monkeypatch):
    from app.core.analyst import runner

    def boom(*a, **k):
        raise runner.AnalystConfigError("kein Key")

    monkeypatch.setattr(runner, "run_analyst", boom)
    sch._execute("manual")
    st = sch.get_status()
    assert st["status"] == "idle" and st["last_error"] == "kein Key" and st["last_status"] == "error"

    class _R:
        run_id, status, error = "r1", "done", None

    monkeypatch.setattr(runner, "run_analyst", lambda *a, **k: _R())
    sch._execute("manual")
    st = sch.get_status()
    assert st["last_run_id"] == "r1" and st["last_status"] == "done" and st["last_error"] is None
