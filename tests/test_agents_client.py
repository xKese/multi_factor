"""Tests für den TradingAgents-HTTP-Client (Faktor-Kontext, Job-Registry, SSE)."""

from __future__ import annotations

import math

import pytest

from app.core import agents_client, persistence
from app.core.config import Settings


@pytest.fixture(autouse=True)
def _clean_registry(monkeypatch):
    agents_client._JOBS.clear()
    agents_client._SEARCH_CACHE.clear()
    monkeypatch.setattr(agents_client, "_catalog_cache", None)
    monkeypatch.setattr(agents_client, "_catalog_failed_at", None)
    yield
    agents_client._JOBS.clear()
    agents_client._SEARCH_CACHE.clear()


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


def _v2_settings() -> Settings:
    s = Settings()
    s.scoring_version = "v2"
    return s


def _scored_row_v2(financial: bool = False) -> dict:
    """Universumszeile nach ``compute_scores_v2`` (Nicht-Fin oder Financial)."""
    row = _scored_row()
    row.update(
        {
            "composite_score": 83.4,
            "composite_pct": 0.834,
            "composite_z": 1.0123456,
            "composite_raw": 0.7,
            "classification_v2": "B+",
            "zone_v2": "KANDIDAT",
            "data_coverage_v2": 0.95,
            "is_financial": financial,
            "is_real_estate": False,
            "z_value": 0.4, "cov_value": 1.0,
            "z_quality": 1.2, "cov_quality": 0.75,
            "z_momentum": 0.9, "cov_momentum": 1.0,
            "z_investment": float("nan"), "cov_investment": 0.0,
            "filter_pass": True,
            "filter_reasons": [],
            "trend_warning": False,
        }
    )
    if financial:
        row.update(
            {
                "pb": 1.1, "z_pb": 0.3, "neut_level_pb": "industry_group",
                "pe": 9.5, "z_pe": 0.5,
                "roe": 0.12, "z_roe": 1.1,
                "accruals": -0.02, "z_accruals": 0.4,
                "mom_12_1_adj": 0.13, "z_mom_12_1_adj": 0.9,
                "share_issuance": 0.01, "z_share_issuance": -0.2,
            }
        )
    else:
        row.update(
            {
                "ev_ebitda": 8.1, "z_ev_ebitda": -0.4, "neut_level_ev_ebitda": "industry_group",
                "fcf_yield_v2": 0.065, "fcf_yield_source": "ev", "z_fcf_yield": 1.2,
                "ev_ebit": float("nan"), "z_ev_ebit": float("nan"),
                "gp_ta": 0.35, "z_gp_ta": 1.4,
                "roic": 0.18, "z_roic": 1.0,
                "accruals": -0.02, "z_accruals": 0.4,
                "debt_ebit": 2.5, "z_debt_ebit": -0.3,  # Leverage-Fallback
                "debt_equity": 0.8,
                "mom_12_1_adj": 0.13, "z_mom_12_1_adj": 0.9,
                "eps_revisions_3m": 0.02, "z_eps_revisions_3m": 0.5,
                "asset_growth": 0.11, "z_asset_growth": float("nan"),
                "share_issuance": float("nan"), "z_share_issuance": float("nan"),
            }
        )
    return row


def test_build_factor_context_shape_and_nan_omission():
    fc = agents_client.build_factor_context(_scored_row())
    assert fc["source"] == "multi_factor"
    assert fc["scoring_version"] == "v1"
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


def test_build_factor_context_v1_settings_keep_legacy_payload():
    """Bei scoring_version=v1 bleibt der Payload v1 — auch wenn die Zeile
    bereits v2-Spalten trägt (kein composite_v2-Block, keine v2-Streufelder)."""
    s = Settings()
    s.scoring_version = "v1"
    fc = agents_client.build_factor_context(_scored_row_v2(), s)
    assert fc["scoring_version"] == "v1"
    assert fc["total_score"] == 78.2
    assert fc["recommendation"] == "BUY"
    assert "composite_v2" not in fc
    assert not {"composite_score", "classification_v2", "zone_v2"} & set(fc)


def test_build_factor_context_v2_replaces_v1_scores():
    """Composite v2 aktiv: die Agenten bekommen den vollständigen v2-Input,
    die alten v1-Score-Felder entfallen; neutrale Daten bleiben."""
    import json

    fc = agents_client.build_factor_context(_scored_row_v2(), _v2_settings())
    assert fc["scoring_version"] == "v2"
    # v1-Score-Felder sind weg …
    v1_keys = {"total_score", "factor_scores", "recommendation",
               "classification", "filter_ok"}
    assert not v1_keys & set(fc)
    # … neutrale Daten bleiben.
    assert fc["piotroski"] == 7.0 and fc["altman_z"] == 4.2
    assert fc["signals"]["sma_signal"] == "GOLDEN CROSS"
    assert fc["identity"]["name"] == "Mercedes-Benz Group AG"
    assert fc["source_ticker"] == "MBG"

    comp = fc["composite_v2"]
    assert comp["score"] == 83.4
    assert comp["pct"] == 0.834
    assert comp["z"] == 1.0123  # auf 4 Stellen gerundet
    assert comp["classification"] == "B+"
    assert comp["zone"] == "KANDIDAT"
    assert comp["zone_thresholds"] == {"entry_pct": 0.80, "exit_pct": 0.667}
    assert comp["data_coverage"] == 0.95
    assert comp["segment"] == "nonfinancial"
    assert comp["filter"] == {"pass": True, "reasons": []}
    assert comp["trend_warning"] is False

    # Faktoren: Z, Abdeckung, strategisches Gewicht; NaN-Z entfällt.
    assert comp["factors"]["quality"] == {
        "z": 1.2, "coverage": 0.75, "weight_strategic": 0.30
    }
    assert "z" not in comp["factors"]["investment"]
    assert comp["factors"]["investment"]["coverage"] == 0.0

    # Indikatoren je Segment (Nicht-Fin): Leverage über debt_ebit aufgelöst,
    # fcf_yield aus der kombinierten Serie inkl. Quelle, ev_ebit (NaN) fehlt.
    ind = comp["indicators"]
    assert ind["ev_ebitda"] == {
        "factor": "value", "direction": -1, "z": -0.4, "value": 8.1,
        "neut_level": "industry_group",
    }
    assert ind["fcf_yield"]["value"] == 0.065 and ind["fcf_yield"]["source"] == "ev"
    assert ind["debt_ebit"]["factor"] == "quality" and ind["debt_ebit"]["direction"] == -1
    assert "debt_equity" not in ind and "net_debt_ebitda" not in ind
    assert "ev_ebit" not in ind
    assert ind["asset_growth"] == {"factor": "investment", "direction": -1, "value": 0.11}
    assert "share_issuance" not in ind
    assert "pb" not in ind and "roe" not in ind

    # Vertrag mit dem Service: JSON-serialisierbar, kein NaN, unter 8 KB.
    text = json.dumps(fc, ensure_ascii=False)
    assert "NaN" not in text
    assert len(text) < 8192


def test_build_factor_context_v2_financial_segment():
    fc = agents_client.build_factor_context(
        _scored_row_v2(financial=True), _v2_settings()
    )
    comp = fc["composite_v2"]
    assert comp["segment"] == "financial"
    ind = comp["indicators"]
    assert set(ind) == {"pb", "pe", "roe", "accruals", "mom_12_1_adj", "share_issuance"}
    assert ind["pb"]["neut_level"] == "industry_group"
    assert ind["roe"] == {"factor": "quality", "direction": 1, "z": 1.1, "value": 0.12}


def test_build_factor_context_v2_without_composite_stays_v2():
    """Zeile ohne berechenbaren Composite (NaN): bei v2 trotzdem kein
    v1-Payload — der Block trägt Faktoren, Indikatoren und Filtergrund."""
    row = _scored_row_v2()
    row["composite_score"] = float("nan")
    row["filter_pass"] = False
    row["filter_reasons"] = ["coverage"]
    fc = agents_client.build_factor_context(row, _v2_settings())
    assert fc["scoring_version"] == "v2"
    assert "total_score" not in fc and "recommendation" not in fc
    comp = fc["composite_v2"]
    assert "score" not in comp
    assert comp["factors"]["quality"]["z"] == 1.2
    assert comp["filter"] == {"pass": False, "reasons": ["coverage"]}
    assert agents_client.primary_quant(fc) == (None, "B+", "v2")


def test_build_factor_context_v2_includes_methodology():
    fc = agents_client.build_factor_context(_scored_row_v2(), _v2_settings())
    meth = fc["composite_v2"]["methodology"]
    assert isinstance(meth, list) and all(isinstance(m, str) for m in meth)
    text = " ".join(meth)
    # Gewichte, Segment-Indikatoren, Neutralisierung, Schwellen, Filter.
    assert "Value 0.30, Quality 0.30, Momentum 0.25, Investment 0.15" in text
    assert "non-financials" in text
    assert "ev_ebitda (lower is better)" in text
    assert "debt_ebit (lower is better)" in text
    assert "global GICS industry group" in text
    assert "winsorised at the 3%/97% quantiles" in text
    assert "A >= 90%, B+ >= 80%, B >= 66.7%, C >= 50%, D >= 33%" in text
    assert "KANDIDAT = composite pct >= 80.0%" in text
    assert "VERKAUFEN = below 66.7%" in text
    assert "market cap >= 1000 M EUR" in text
    assert "Piotroski >= 5/9" in text
    # Financials: eigener Indikatorsatz und Mindestabdeckung je 1.
    fc_fin = agents_client.build_factor_context(
        _scored_row_v2(financial=True), _v2_settings()
    )
    text_fin = " ".join(fc_fin["composite_v2"]["methodology"])
    assert "(financials)" in text_fin
    assert "Value: pb (lower is better), pe (lower is better)" in text_fin
    assert "Value 1, Quality 1, Momentum 1, Investment 1" in text_fin


def test_primary_quant_and_quant_direction():
    v2 = {"composite_v2": {"score": 83.4, "classification": "B+", "zone": "HALTEN"}}
    assert agents_client.primary_quant(v2) == (83.4, "B+", "v2")
    assert agents_client.quant_direction(v2) == "hold"
    assert agents_client.quant_direction(
        {"composite_v2": {"score": 10.0, "zone": "FILTER"}}
    ) is None
    assert agents_client.quant_direction(
        {"composite_v2": {"score": 90.0, "zone": "KANDIDAT"}}
    ) == "buy"
    assert agents_client.quant_direction(
        {"composite_v2": {"score": 20.0, "zone": "VERKAUFEN"}}
    ) == "sell"

    v1 = {"total_score": 78.2, "classification": "B+ · Sehr Gut", "recommendation": "BUY"}
    assert agents_client.primary_quant(v1) == (78.2, "B+ · Sehr Gut", "v1")
    assert agents_client.quant_direction(v1) == "buy"
    assert agents_client.primary_quant(None) == (None, None, "v1")
    assert agents_client.quant_direction({}) is None


def test_persist_result_stores_primary_score_of_context_version(monkeypatch):
    saved = {}
    monkeypatch.setattr(persistence, "save_agent_analysis", lambda rec: saved.update(rec))
    fc = {"scoring_version": "v2", "composite_v2": {"score": 83.4, "classification": "B+"}}
    agents_client._persist_result(
        "MBG", "MBG.F", _payload(), fc, True, "run-1", "Overweight", {}
    )
    assert saved["total_score"] == 83.4
    assert saved["classification"] == "B+"
    assert saved["factor_context"] is fc


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


def test_symbol_search_caches_successful_lookups(monkeypatch):
    calls = []

    class _Resp:
        status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {"results": [{"symbol": "MBG.F", "name": "Mercedes"}], "note": None}

    def _get(url, **kwargs):
        calls.append(kwargs.get("params", {}).get("q"))
        return _Resp()

    monkeypatch.setattr(agents_client.requests, "get", _get)

    r1, note1 = agents_client.symbol_search("mercedes")
    r2, _ = agents_client.symbol_search("Mercedes")  # Groß-/Klein → gleicher Key
    assert r1 == r2 == [{"symbol": "MBG.F", "name": "Mercedes"}]
    assert note1 is None
    assert len(calls) == 1  # zweiter Aufruf kommt aus dem Cache

    # Fehler werden nicht gecacht: Service down → jeder Aufruf versucht es neu.
    def _down(url, **kwargs):
        calls.append("down")
        raise ConnectionError("down")

    monkeypatch.setattr(agents_client.requests, "get", _down)
    results, note = agents_client.symbol_search("apple")
    assert results == [] and "nicht erreichbar" in note
    agents_client.symbol_search("apple")
    assert calls.count("down") == 2


def test_current_agent_prefers_in_progress():
    job = {
        "agent_states": {
            "Market Analyst": "completed",
            "Bull Researcher": "in_progress",
        },
        "stage": "Bull Researcher (in_progress)",
    }
    assert agents_client.current_agent(job) == "Bull Researcher"
    # Ohne in_progress-Agent fällt die Anzeige auf die Stufe zurück.
    assert (
        agents_client.current_agent({"stage": "Starte Analyse …"})
        == "Starte Analyse …"
    )
    assert agents_client.current_agent({}) is None


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


def test_build_run_payload_sends_output_language(monkeypatch):
    monkeypatch.setattr(
        agents_client,
        "get_catalog",
        lambda force=False: {
            "defaults": {
                "llm_provider": "ollama",
                "quick_think_llm": "m",
                "deep_think_llm": "m",
            }
        },
    )
    # Default der App: deutsche Reports.
    payload, err = agents_client.build_run_payload("AAPL", None, Settings())
    assert err is None
    assert payload["output_language"] == "German"

    # In den Einstellungen gewählte Sprache wird durchgereicht.
    s = Settings()
    s.agents_language = "English"
    payload2, _ = agents_client.build_run_payload("AAPL", None, s)
    assert payload2["output_language"] == "English"

    # Leerer Wert: Feld weglassen, der Service-Default (English) greift.
    s2 = Settings()
    s2.agents_language = ""
    payload3, _ = agents_client.build_run_payload("AAPL", None, s2)
    assert "output_language" not in payload3


def test_build_run_payload_sends_temperature(monkeypatch):
    monkeypatch.setattr(
        agents_client,
        "get_catalog",
        lambda force=False: {
            "defaults": {
                "llm_provider": "ollama",
                "quick_think_llm": "m",
                "deep_think_llm": "m",
            }
        },
    )
    # App-Default: Temperatur 0 (deterministisch).
    payload, err = agents_client.build_run_payload("AAPL", None, Settings())
    assert err is None
    assert payload["temperature"] == 0.0

    # Einstellungs-Override wird durchgereicht und auf [0, 2] begrenzt.
    s = Settings()
    s.agents_temperature = 0.7
    assert agents_client.build_run_payload("AAPL", None, s)[0]["temperature"] == 0.7
    s.agents_temperature = 9
    assert agents_client.build_run_payload("AAPL", None, s)[0]["temperature"] == 2.0

    # None hieße: Feld weglassen, Service-Default greift.
    s2 = Settings()
    s2.agents_temperature = None
    assert "temperature" not in agents_client.build_run_payload("AAPL", None, s2)[0]


def test_build_run_payload_sends_previous_analysis_flag(monkeypatch):
    monkeypatch.setattr(
        agents_client,
        "get_catalog",
        lambda force=False: {
            "defaults": {
                "llm_provider": "ollama",
                "quick_think_llm": "m",
                "deep_think_llm": "m",
            }
        },
    )
    # App-Default: Vergleich mit letzter Analyse aktiv.
    payload, err = agents_client.build_run_payload("AAPL", None, Settings())
    assert err is None
    assert payload["previous_analysis_enabled"] is True

    # Abgeschaltet in den Einstellungen → explizit False im Payload.
    s = Settings()
    s.agents_prev_analysis = False
    payload2, _ = agents_client.build_run_payload("AAPL", None, s)
    assert payload2["previous_analysis_enabled"] is False


def test_friendly_error_flags_transient_provider_failures():
    """Regression: Ollama Clouds 500 („Internal Server Error (ref: …)") kam
    als rohe Exception in der UI an — ohne Hinweis, dass der Fehler beim
    LLM-Anbieter lag und ein Neustart genügt."""
    msg = (
        "InternalServerError: Error code: 500 - "
        "{'error': 'Internal Server Error (ref: eddcd20d-c829-4b09)'}"
    )
    out = agents_client._friendly_error(msg)
    assert "vorübergehender Fehler beim LLM-Anbieter" in out
    assert "neu gestartet" in out
    assert "Error code: 500" in out  # Original bleibt für die Diagnose sichtbar

    # Auch 502/503 und Timeouts gelten als transient.
    assert "LLM-Anbieter" in agents_client._friendly_error("Error code: 503 - overloaded")
    assert "LLM-Anbieter" in agents_client._friendly_error("Read timed out")


def test_friendly_error_leaves_other_messages_untouched():
    assert agents_client._friendly_error("Ungültige Research-Tiefe.") == "Ungültige Research-Tiefe."
    assert agents_client._friendly_error(None) == "Unbekannter Fehler"
    assert agents_client._friendly_error("") == "Unbekannter Fehler"
