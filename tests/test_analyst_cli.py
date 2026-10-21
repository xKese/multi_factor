"""Tests der KI-Analyst-CLI (Exit-Codes, Subkommandos) ohne Netzwerk."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import pytest

from app.core.analyst import runner as runner_mod
from app.core.state import STATE
from app.tools import analyst as cli


@dataclass
class _Budget:
    llm_calls: int = 2
    av_calls: int = 5


@dataclass
class _Result:
    run_id: str = "20261021_090000"
    status: str = "done"
    dry_run: bool = False
    companies: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    trends: list = field(default_factory=list)
    report_path: str | None = "reports/x.md"
    error: str | None = None
    budget: _Budget = field(default_factory=_Budget)
    n_events: int = 3
    diagnostics: list = field(default_factory=list)

    def all_diagnostics(self):
        return self.diagnostics


@pytest.fixture()
def state(monkeypatch):
    monkeypatch.setattr(STATE, "load_from_db", lambda: True)
    monkeypatch.setattr(STATE, "scored", pd.DataFrame({"uid": ["SAP"], "ticker": ["SAP"], "name": ["SAP SE"]}), raising=False)
    return STATE


def test_run_ok_and_exit_codes(state, monkeypatch, capsys):
    captured = {}

    def fake_run(settings, scored, st, **kw):
        captured.update(kw)
        return _Result()

    monkeypatch.setattr(runner_mod, "run_analyst", fake_run)
    assert cli.main(["run", "--tickers", "SAP, MBG", "--limit", "3", "--dry-run", "--no-trends"]) == 0
    assert captured["scope"] == "custom" and captured["tickers"] == ["SAP", "MBG"]
    assert captured["limit"] == 3 and captured["dry_run"] is True and captured["no_trends"] is True
    out = capsys.readouterr().out
    assert "Lauf 20261021_090000: Status done" in out and "Report: reports/x.md" in out

    monkeypatch.setattr(runner_mod, "run_analyst", lambda *a, **k: _Result(status="error", error="kaputt"))
    assert cli.main(["run"]) == 1

    def raise_cfg(*a, **k):
        raise runner_mod.AnalystConfigError("kein Key")

    monkeypatch.setattr(runner_mod, "run_analyst", raise_cfg)
    assert cli.main(["run"]) == 1
    assert "kein Key" in capsys.readouterr().err


def test_run_without_universe(monkeypatch):
    monkeypatch.setattr(STATE, "load_from_db", lambda: True)
    monkeypatch.setattr(STATE, "scored", None, raising=False)
    assert cli.main(["run"]) == 1


def test_argument_errors(state, monkeypatch):
    STATE.settings.analyst_custom_tickers = []
    assert cli.main(["run", "--scope", "custom"]) == 2
    assert cli.main(["run", "--scope", "weird"]) == 2
    assert cli.main([]) == 2


def test_models_command(monkeypatch, capsys):
    from app.core.analyst import ollama_client

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    assert cli.main(["models"]) == 1
    monkeypatch.setenv("OLLAMA_API_KEY", "k")
    monkeypatch.setattr(ollama_client, "list_models", lambda: ["a:1", "b:2"])
    assert cli.main(["models"]) == 0
    assert capsys.readouterr().out.splitlines() == ["a:1", "b:2"]
    monkeypatch.setattr(ollama_client, "list_models", lambda: [])
    assert cli.main(["models"]) == 1


def test_status_themes_trends(monkeypatch, capsys):
    from app.core.analyst import store

    monkeypatch.setattr(store, "list_runs", lambda limit=10: [])
    assert cli.main(["status"]) == 0
    assert "Noch keine Läufe" in capsys.readouterr().out
    monkeypatch.setattr(store, "list_runs", lambda limit=10: [{"run_id": "r1", "status": "done", "trigger": "cli", "started_at": datetime(2026, 10, 21, 9), "n_companies": 2, "n_events": 3, "n_llm_calls": 4, "n_av_calls": 5, "n_themes": 1, "n_trends": 1, "dry_run": False, "error": None}])
    assert cli.main(["status", "--limit", "1"]) == 0
    assert "r1  done" in capsys.readouterr().out

    monkeypatch.setattr(store, "list_themes", lambda run_id=None: [{"title": "T", "kind": "makro", "direction": "positiv", "confidence": 0.7, "description": "d", "uids": ["SAP"], "confirmation": "x"}])
    assert cli.main(["themes", "--run", "r1"]) == 0
    assert "- T [makro" in capsys.readouterr().out
    monkeypatch.setattr(store, "list_trends", lambda run_id=None: [{"title": "Tr", "kind": "makro", "horizon": "mittel", "strength": 4, "confidence": 0.8, "description": "d", "links": [{"ticker": "SAP", "role": "direkt", "pricing_quant": "früh", "pricing_llm": None, "action": "Watchlist"}]}])
    assert cli.main(["trends"]) == 0
    out = capsys.readouterr().out
    assert "- Tr [makro" in out and "SAP" in out and "Watchlist" in out
    monkeypatch.setattr(store, "list_trends", lambda run_id=None: [])
    assert cli.main(["trends"]) == 0


def test_watchlist_command(state, monkeypatch, capsys):
    from app.core.analyst import store, watchlist

    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    monkeypatch.setattr(store, "load_watch_states", lambda uids: {})
    monkeypatch.setattr(watchlist, "build_watchlist", lambda *a, **k: ([watchlist.WatchItem("SAP", "SAP", "SAP SE", zone="KANDIDAT", composite_score=88.0, reasons={"kandidat"})], []))
    assert cli.main(["watchlist", "--limit", "5"]) == 0
    out = capsys.readouterr().out
    assert "1 Titel" in out and "SAP" in out and "kandidat" in out
