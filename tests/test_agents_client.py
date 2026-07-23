"""Tests für den TradingAgents-HTTP-Client (Faktor-Kontext, Job-Registry, SSE)."""

from __future__ import annotations

import math

import pytest

from app.core import agents_client, persistence
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    agents_client._JOBS.clear()
    monkeypatch.setattr(agents_client, "_catalog_cache", None)
    monkeypatch.setattr(agents_client, "_catalog_failed_at", None)
    yield
    agents_client._JOBS.clear()


def _scored_row() -> dict:
    return {
        "ticker": "MBG",
        "name": "Mercedes-Benz Group AG",
        "sector": "Consumer Discretionary",
        "industry": "Automobiles",
        "region": "Deutschland",
        "market_cap": 65000.0,
        "export_date": "2026-07-20",
        "total_score": 78.2,
        "value_score": 45.1,
        "quality_score": 88.0,
        "growth_score": 71.3,
        "momentum_score": 65.0,
        "lowvol_score": float("nan"),
        "classification": "B+ · Sehr Gut",
        "filter_ok": "JA",
        "recommendation": "BUY",
        "piotroski": 7.0,
        "altman_z": 4.2,
        "sma_signal": "GOLDEN CROSS",
        "trend_phase": "Bulle (etabliert)",
        "mom_12_1": 0.18123,
        "dist_52w_high": float("nan"),
    }


def test_build_factor_context_shape_and_nan_omission():
    fc = agents_client.build_factor_context(_scored_row())
    assert fc["source"] == "multi_factor"
    assert fc["as_of"] == "2026-07-20"
    assert fc["total_score"] == 78.2
    assert fc["source_ticker"] == "MBG"
    # NaN-Felder werden weggelassen (lowvol_score, dist_52w_high).
    assert "lowvol" not in fc["factor_scores"]
    assert "dist_52w_high" not in fc["signals"]
    assert fc["signals"]["mom_12_1"] == 0.1812
    assert fc["identity"]["region"] == "Deutschland"
    # Ergebnis muss ohne NaN JSON-serialisierbar sein.
    import json

    assert not any(
        isinstance(v, float) and math.isnan(v)
        for v in json.loads(json.dumps(fc)).values()
        if isinstance(v, float)
    )


class _FakeSSEResponse:
    status_code = 200

    def __init__(self, lines):
        self._lines = lines

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def iter_lines(self, decode_unicode=True):
        yield from self._lines


_SSE_OK = [
    "event: run",
    'data: {"ticker": "MBG.F", "agents": ["Market Analyst", "Trader"]}',
    "",
    "event: status",
    'data: {"agent": "Market Analyst", "status": "in_progress"}',
    "",
    "event: status",
    'data: {"agent": "Market Analyst", "status": "completed"}',
    "",
    "event: final",
    'data: {"decision": "Overweight", "run_id": "MBG.F_20260723_120000", '
    '"reports": {"final_trade_decision": "**Rating**: Overweight"}}',
    "",
    "event: done",
    "data: {}",
    "",
]


def _payload() -> dict:
    return {
        "ticker": "MBG.F",
        "analysis_date": "2026-07-23",
        "llm_provider": "anthropic",
        "shallow_thinker": "q",
        "deep_thinker": "d",
        "research_depth": 1,
    }


def test_run_job_success_persists_and_completes(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        persistence, "save_agent_analysis", lambda rec: saved.update(rec)
    )
    monkeypatch.setattr(
        agents_client.requests,
        "post",
        lambda *a, **k: _FakeSSEResponse(_SSE_OK),
    )

    agents_client._set_job("MBG", status="running")
    agents_client._run_job(
        "MBG", "MBG.F", _payload(), {"total_score": 78.2}, in_universe=True
    )

    job = agents_client.get_status("MBG")
    assert job["status"] == "done"
    assert job["run_id"] == "MBG.F_20260723_120000"
    assert job["agent_states"]["Market Analyst"] == "completed"
    assert saved["ticker"] == "MBG"
    assert saved["agents_ticker"] == "MBG.F"
    assert saved["rating"] == "Overweight"
    assert saved["reports"]["final_trade_decision"].startswith("**Rating**")
    assert saved["total_score"] == 78.2


def test_run_job_error_event(monkeypatch):
    monkeypatch.setattr(
        persistence, "save_agent_analysis", lambda rec: None
    )
    lines = [
        "event: error",
        'data: {"message": "Kein API-Key gesetzt."}',
        "",
    ]
    monkeypatch.setattr(
        agents_client.requests, "post", lambda *a, **k: _FakeSSEResponse(lines)
    )

    agents_client._set_job("MBG", status="running")
    agents_client._run_job("MBG", "MBG.F", _payload(), None, in_universe=False)

    job = agents_client.get_status("MBG")
    assert job["status"] == "error"
    assert "API-Key" in job["error"]


def test_run_job_connection_drop_recovers_from_archive(monkeypatch):
    saved = {}
    monkeypatch.setattr(
        persistence, "save_agent_analysis", lambda rec: saved.update(rec)
    )

    def _broken_post(*a, **k):
        raise ConnectionError("Verbindung abgerissen")

    class _FakeGetResponse:
        status_code = 200

        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            pass

        def json(self):
            return self._data

    future = "2999-01-01T00:00:00+00:00"
    responses = {
        "/api/reports": {
            "runs": [
                {"id": "MBG.F_29990101_000000", "ticker": "MBG.F", "created_at": future}
            ]
        },
        "/api/reports/MBG.F_29990101_000000": {
            "id": "MBG.F_29990101_000000",
            "decision": "Hold",
            "reports": {"final_trade_decision": "**Rating**: Hold"},
        },
    }

    def _fake_get(url, **kwargs):
        for suffix, data in responses.items():
            if url.endswith(suffix):
                return _FakeGetResponse(data)
        raise AssertionError(f"Unerwartete URL: {url}")

    monkeypatch.setattr(agents_client.requests, "post", _broken_post)
    monkeypatch.setattr(agents_client.requests, "get", _fake_get)

    agents_client._set_job("MBG", status="running")
    agents_client._run_job("MBG", "MBG.F", _payload(), None, in_universe=True)

    job = agents_client.get_status("MBG")
    assert job["status"] == "done"
    assert job["run_id"] == "MBG.F_29990101_000000"
    assert saved["rating"] == "Hold"


def test_list_jobs_snapshot_newest_first():
    agents_client._set_job("AAA", status="done", started_at=100.0)
    agents_client._set_job("BBB", status="running", started_at=200.0)

    jobs = agents_client.list_jobs()
    assert list(jobs) == ["BBB", "AAA"]
    assert jobs["BBB"]["status"] == "running"

    # Snapshot ist entkoppelt: Mutation ändert die Registry nicht.
    jobs["BBB"]["status"] = "mutiert"
    assert agents_client.get_status("BBB")["status"] == "running"


def test_start_analysis_refuses_concurrent_runs(monkeypatch):
    agents_client._set_job("AAA", status="running")

    ok, msg = agents_client.start_analysis("AAA", "AAA", Settings())
    assert not ok
    assert "läuft bereits" in msg

    ok, msg = agents_client.start_analysis("BBB", "BBB", Settings())
    assert not ok
    assert "AAA" in msg


def test_start_analysis_requires_reachable_service(monkeypatch):
    def _down(*a, **k):
        raise ConnectionError("down")

    monkeypatch.setattr(agents_client.requests, "get", _down)
    ok, msg = agents_client.start_analysis("MBG", "MBG.F", Settings())
    assert not ok
    assert "nicht erreichbar" in msg


def test_build_run_payload_uses_settings_over_defaults(monkeypatch):
    monkeypatch.setattr(
        agents_client,
        "get_catalog",
        lambda force=False: {
            "defaults": {
                "llm_provider": "ollama",
                "quick_think_llm": "qwen3",
                "deep_think_llm": "qwen3",
            }
        },
    )
    s = Settings()
    s.agents_provider = "anthropic"
    s.agents_quick_model = "claude-haiku-4-5-20251001"
    s.agents_deep_model = "claude-sonnet-5"
    s.agents_depth = 3

    payload, err = agents_client.build_run_payload("MBG.F", {"total_score": 78.2}, s)
    assert err is None
    assert payload["llm_provider"] == "anthropic"
    assert payload["research_depth"] == 3
    assert payload["factor_context"]["total_score"] == 78.2

    # Leere Einstellungen fallen auf die Service-Defaults zurück.
    payload2, err2 = agents_client.build_run_payload("MBG.F", None, Settings())
    assert err2 is None
    assert payload2["llm_provider"] == "ollama"
    assert "factor_context" not in payload2
    # Kein backend_url im Katalog → Feld weglassen (Server nimmt Provider-Default).
    assert "backend_url" not in payload2


def test_build_run_payload_forwards_backend_url(monkeypatch):
    # Spiegelt die Browser-UI: die env-bewusste defaults.backend_url des
    # Service (TRADINGAGENTS_LLM_BACKEND_URL) wird in den Payload übernommen.
    monkeypatch.setattr(
        agents_client,
        "get_catalog",
        lambda force=False: {
            "defaults": {
                "llm_provider": "openai_compatible",
                "quick_think_llm": "m",
                "deep_think_llm": "m",
                "backend_url": "http://host.docker.internal:1234/v1",
            }
        },
    )
    payload, err = agents_client.build_run_payload("AAPL", None, Settings())
    assert err is None
    assert payload["backend_url"] == "http://host.docker.internal:1234/v1"
