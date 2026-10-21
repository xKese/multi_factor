"""End-to-End-Tests des Runners mit Fake-AV, Fake-LLM und frischer SQLite."""

from __future__ import annotations

import importlib
import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from app.core import av_client
from app.core.av_client import AlphaVantageError
from app.core.config import Settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"
NOW = datetime(2026, 10, 21, 9, 0, 0)


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _scored():
    return pd.DataFrame(
        {
            "uid": ["SAP", "MBG", "NOAV", "CG1"],
            "ticker": ["SAP", "MBG", "NOAV", "CG1"],
            "name": ["SAP SE", "Mercedes-Benz Group AG", "No AV Corp", "Capital One"],
            "sector": ["IT", "Consumer", "IT", "Industrials"],
            "industry": ["Software", "Autos", "Software", "Machinery"],
            "industry_group": ["Software & Services", "Automobiles & Components", "Software & Services", "Capital Goods"],
            "region": ["EU", "EU", "US", "US"],
            "filter_pass": [True, True, True, True],
            "zone_v2": ["KANDIDAT", "HALTEN", "KANDIDAT", "KANDIDAT"],
            "composite_score": [88.0, 70.0, 82.0, 80.0],
            "composite_pct": [0.9, 0.7, 0.85, 0.8],
            "z_value": [1.2, 0.1, 0.0, 0.3],
            "z_quality": [0.5, 0.2, 0.0, 0.1],
            "z_momentum": [-1.0, 0.3, 0.0, 0.1],
            "z_investment": [0.2, 0.1, 0.0, 0.0],
            "z_eps_revisions_3m": [0.0, 0.0, 0.0, 0.0],
            "mom_12_1": [0.05, 0.1, 0.0, 0.0],
            "ret_1m": [-0.02, 0.01, 0.0, 0.0],
            "export_date": [date(2026, 10, 17)] * 4,
        }
    )


class _State:
    def resolve_portfolio(self):
        return pd.DataFrame({"uid": ["MBG"], "status": ["ok"]})


class FakeAV:
    """Ersetzt ``runner.av``; Hilfsfunktionen delegieren ans echte Modul."""

    def __init__(self, rate_limit_at: str | None = None):
        from app.core import av_fundamentals as real

        self.real = real
        self.calls: list[tuple] = []
        self.rate_limit_at = rate_limit_at
        self.latest_reported_quarter = real.latest_reported_quarter
        self.default_news_window = real.default_news_window
        self.quarter_label = real.quarter_label

    def _check(self, fn, symbol):
        self.calls.append((fn, symbol))
        if self.rate_limit_at and symbol == self.rate_limit_at:
            raise AlphaVantageError("limit", kind="rate_limit")

    def fetch_earnings(self, symbol, requests_per_minute=70):
        self._check("earnings", symbol)
        return self.real.fetch_earnings.__wrapped__(symbol) if hasattr(self.real.fetch_earnings, "__wrapped__") else _earnings_rows()

    def fetch_transcript(self, symbol, quarter, requests_per_minute=70):
        self._check("transcript", symbol)
        if symbol == "MBG.DEX":
            return None
        data = _load("av_transcript_sample.json")
        return {"symbol": symbol, "quarter": quarter, "segments": [
            {"speaker": s["speaker"], "title": s["title"], "content": s["content"], "sentiment": None}
            for s in data["transcript"] if s["content"]
        ]}

    def fetch_insider_transactions(self, symbol, since=None, requests_per_minute=70):
        self._check("insider", symbol)
        return [{"transaction_date": date(2026, 10, 15), "executive": "Jane", "executive_title": "CEO",
                 "security_type": "Common", "acquisition_or_disposal": "A", "shares": 5000, "share_price": 100.0}]

    def fetch_news_sentiment(self, tickers=None, topics=None, time_from=None, limit=50, sort="LATEST", requests_per_minute=70):
        sym = tickers[0] if tickers else topics[0]
        self._check("news", sym)
        if topics:
            return [{"title": f"Markt {topics[0]}", "url": f"u-{topics[0]}", "time_published": datetime(2026, 10, 20),
                     "summary": "s", "source": "Src", "overall_sentiment_label": "Neutral", "overall_sentiment_score": 0.0,
                     "topics": [{"topic": topics[0], "relevance_score": 1.0}], "ticker_sentiment": []}]
        return [{"title": "Firmen-News", "url": f"n-{sym}", "time_published": datetime(2026, 10, 20, 14, 30),
                 "summary": "s", "source": "Src", "overall_sentiment_label": "Bullish", "overall_sentiment_score": 0.4,
                 "topics": [], "ticker_sentiment": [{"ticker": sym, "relevance_score": 0.9, "sentiment_score": 0.5, "sentiment_label": "Bullish"}]}]

    def fetch_earnings_calendar(self, symbol=None, horizon="3month", requests_per_minute=70):
        self._check("calendar", symbol)
        return [{"symbol": "SAP.DEX", "name": "SAP", "report_date": date(2026, 10, 28), "fiscal_date_ending": None, "estimate": 1.7, "currency": "EUR"}]


def _earnings_rows():
    return [
        {"fiscal_date_ending": date(2026, 6, 30), "reported_date": date(2026, 7, 28), "reported_eps": 1.62,
         "estimated_eps": 1.55, "surprise": 0.07, "surprise_pct": 4.5},
    ]


class FakeLLM:
    def __init__(self, fail_after: int | None = None):
        self.calls: list[dict] = []
        self.fail_after = fail_after

    def chat_json(self, system, user, schema, required, model="", temperature=0.0, timeout=300):
        from app.core.analyst import prompts
        from app.core.analyst.ollama_client import OllamaError

        self.calls.append({"schema": schema, "user": user, "model": model})
        if self.fail_after is not None and len(self.calls) > self.fail_after:
            raise OllamaError("down", kind="network")
        if schema is prompts.TRANSCRIPT_SCHEMA:
            out = {"zusammenfassung": "Starkes Quartal.", "kernaussagen": ["Rekordumsatz"],
                   "guidance": {"aenderung": "angehoben", "details": "9,8–10,0 Mrd."}, "ton": 4, "vertrauen_management": 4,
                   "glaubwuerdigkeit_flags": [], "risiken": ["Preisdruck EU"], "qa_highlights": ["Backlog stark"],
                   "quant_abgleich": {"urteil": "widerspricht", "begruendung": "Momentum schwach trotz Guidance"},
                   "themen_kandidaten": [{"thema": "Kapazitätsausbau", "zitat": "Capex will increase", "art": "kapitalallokation"}],
                   "antworten_prueffragen": [{"code": "value_trap", "befund": "temporär", "belegt": True}]}
        elif schema is prompts.NEWS_SCHEMA:
            out = {"zusammenfassung": "Guidance-News.", "themen": [{"titel": "Guidance", "bewertung": "positiv", "quellen": [1]}],
                   "wesentlichkeit": 3, "quant_abgleich": {"urteil": "neutral", "begruendung": ""}, "themen_kandidaten": []}
        elif schema is prompts.SYNTHESIS_SCHEMA:
            out = _load("ollama_synthesis_sample.json")
            out["nicht_eingepreiste_themen"][0]["thema"] = "Kapazitätsausbau " + str(len(self.calls))
        elif schema is prompts.THEMES_SCHEMA:
            out = _load("ollama_themes_sample.json")
        elif schema is prompts.TREND_SCHEMA:
            out = json.loads(_load.__globals__["json"].dumps(_load("ollama_trends_b_sample.json")).replace("CG0", "CG1"))
        elif schema is prompts.TREND_GROUPS_SCHEMA:
            out = _load("ollama_trends_a_sample.json")
        else:
            out = {"kernaussagen": ["x"]}
        return out, json.dumps(out), 1


class _Resolved:
    def __init__(self, sym):
        self.av_symbol = sym


class FakeMD:
    @staticmethod
    def resolve_symbols(tickers, universe, settings, persist=True):
        mapping = {"SAP": "SAP.DEX", "MBG": "MBG.DEX", "CG1": "CG1"}
        return {t: _Resolved(mapping[t]) for t in tickers if t in mapping}, [t for t in tickers if t not in mapping]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/analyst.db")
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "av-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "ol-key")
    from app.core import persistence
    from app.core.analyst import runner, store, tasks

    importlib.reload(persistence)
    importlib.reload(store)
    fake_av = FakeAV()
    fake_llm = FakeLLM()
    monkeypatch.setattr(runner, "av", fake_av)
    monkeypatch.setattr(runner, "market_data", FakeMD)
    monkeypatch.setattr(tasks, "ollama", fake_llm)
    idx = pd.to_datetime(["2026-07-27", "2026-07-28", "2026-10-20"])
    monkeypatch.setattr(av_client, "fetch_daily_adjusted",
                        lambda sym, outputsize="compact", rpm=70: pd.DataFrame({"adj_close": [100.0, 102.0, 99.0], "close": [100.0, 102.0, 99.0]}, index=idx))
    s = Settings()
    s.analyst_trend_topics = ["technology", "energy_transportation"]
    s.analyst_report_dir = str(tmp_path / "reports")
    return {"runner": runner, "store": store, "av": fake_av, "llm": fake_llm, "settings": s, "tmp": tmp_path, "tasks": tasks}


def test_full_run_writes_everything(env):
    r = env["runner"]
    res = r.run_analyst(env["settings"], _scored(), _State(), trigger="cli", now=NOW)

    assert res.status == "done", res.all_diagnostics()
    assert res.run_id == "20261021_090000"
    assert [c.item.uid for c in res.companies] == ["MBG", "SAP", "NOAV", "CG1"]  # Bestand zuerst, dann Score
    # NOAV: kein AV-Symbol → nur Quant-Pfad, Warnung
    assert any(d.code == "av_symbol_unresolved" and d.uid == "NOAV" for d in res.diagnostics)
    noav = next(c for c in res.companies if c.item.uid == "NOAV")
    assert noav.events == [] and noav.insights == {}
    # SAP: Earnings, Transkript, Insider, News, Berichtstermin
    sap = next(c for c in res.companies if c.item.uid == "SAP")
    kinds = {e.kind for e in sap.events}
    assert kinds == {"earnings", "transcript", "insider", "news", "earnings_upcoming"}
    assert sap.market_reaction["ret_since_report"] == pytest.approx(99 / 102 - 1, abs=1e-4)
    assert [h.code for h in sap.hints] == ["value_trap"]
    assert set(sap.insights) == {"transcript_summary", "news_digest", "synthesis"}
    assert sap.suggestion["suggestion_type"] == "ÜBERPRÜFEN"  # HALTEN ohne Bestand → ÜBERPRÜFEN
    assert sap.llm_calls == 3
    # MBG: kein Transkript → INFO, Cursor fortgeschrieben; HALTEN bleibt (Bestand)
    mbg = next(c for c in res.companies if c.item.uid == "MBG")
    assert any(d.code == "transcript_unavailable" for d in mbg.diagnostics)
    assert mbg.suggestion["suggestion_type"] == "HALTEN"
    assert "transcript_summary" not in mbg.insights

    st = env["store"]
    assert st.load_watch_state("MBG")["last_transcript_quarter"] == "2026Q2"
    sap_state = st.load_watch_state("SAP")
    assert sap_state["last_transcript_quarter"] == "2026Q2"
    assert sap_state["last_earnings_report_date"] == date(2026, 7, 28)
    assert sap_state["last_news_at"] == NOW and sap_state["last_checked_at"] == NOW
    assert sap_state["last_zone"] == "KANDIDAT" and sap_state["av_symbol"] == "SAP.DEX"
    assert st.has_insight("SAP", "transcript_summary", "2026Q2")
    assert len(st.list_events()) == res.n_events > 0
    assert set(st.load_latest_suggestions()["uid"]) == {"SAP", "MBG", "CG1"}
    assert st.load_company_links_for("SAP")

    # Run-Ebene: Themen (3 Beitragsfirmen) + Trends
    assert len(res.themes) == 1 and st.list_themes()[0]["title"] == "KI-Capex-Zyklus"
    assert res.cross_links and st.load_company_links_for("MBG")
    assert len(res.trends) == 1 and st.list_trends()[0]["links"]
    assert res.trend_links[0]["uid"] == "CG1" and res.trend_links[0]["action"] == "Kandidat prüfen"
    run = st.load_run(res.run_id)
    assert run["status"] == "done" and run["n_llm_calls"] == res.budget.llm_calls
    assert run["n_trends"] == 1 and run["n_themes"] == 1
    assert Path(res.report_path).exists() and Path(res.report_path).name == f"ki_analyst_{res.run_id}.md"
    text = Path(res.report_path).read_text(encoding="utf-8")
    for sec in ("## Trends & Profiteure", "## Themen (Universum)", "## Vorschläge", "**Faktor-Zusammenspiel**",
                "**Quellenübergreifende Zusammenhänge**", "**Nicht eingepreiste Themen**", "**Verbundene Unternehmen**",
                "## Firmenübergreifende Verknüpfungen", "## Ereignisse", "## Diagnose", "Entscheidungsunterstützung"):
        assert sec in text, sec
    # Markt-News: ein Call je Topic
    assert [c for c in env["av"].calls if c[0] == "news" and c[1] in ("technology", "energy_transportation")]
    assert env["av"].calls.count(("calendar", None)) == 1


def test_second_run_is_idempotent(env):
    r = env["runner"]
    s = env["settings"]
    r.run_analyst(s, _scored(), _State(), now=NOW)
    n_llm_first = len(env["llm"].calls)
    env["llm"].calls.clear()
    env["av"].calls.clear()
    res2 = r.run_analyst(s, _scored(), _State(), now=datetime(2026, 10, 22, 9, 0, 0))
    assert res2.status == "done"
    # Kein Transkript-Call mehr (Quartal bekannt), keine neuen Earnings-/News-/Insider-Ereignisse,
    # damit auch keine Synthese; Run-Ebene ohne Beiträge → nur Trend-Scan.
    assert ("transcript", "SAP.DEX") not in env["av"].calls
    sap = next(c for c in res2.companies if c.item.uid == "SAP")
    assert sap.events == [] or all(e.kind == "earnings_upcoming" for e in sap.events)
    schemas = [c["schema"] for c in env["llm"].calls]
    from app.core.analyst import prompts

    assert prompts.TRANSCRIPT_SCHEMA not in schemas
    assert len(env["llm"].calls) < n_llm_first
    assert env["store"].list_events() and len(env["store"].list_runs()) == 2


def test_dry_run_makes_no_llm_calls_and_keeps_cursor(env):
    r = env["runner"]
    res = r.run_analyst(env["settings"], _scored(), _State(), dry_run=True, now=NOW)
    assert res.status == "done" and res.dry_run
    assert env["llm"].calls == [] and res.budget.llm_calls == 0
    assert env["store"].load_watch_state("SAP") is None
    assert env["store"].list_events() == []
    assert res.trend_inputs["n_news"] == 2 and res.trend_inputs["n_candidates"] == 4
    text = Path(res.report_path).read_text(encoding="utf-8")
    assert "Trend-Scan-Eingaben" in text and res.report_path.endswith("_dry.md")
    assert "Regelbasierte Hinweise" in text


def test_dry_run_without_ollama_key(env, monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY")
    r = env["runner"]
    res = r.run_analyst(env["settings"], _scored(), _State(), dry_run=True, now=NOW)
    assert res.status == "done"
    with pytest.raises(r.AnalystConfigError):
        r.run_analyst(env["settings"], _scored(), _State(), now=NOW)


def test_config_errors(env, monkeypatch):
    r = env["runner"]
    with pytest.raises(r.AnalystConfigError):
        r.run_analyst(env["settings"], pd.DataFrame(), _State())
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY")
    with pytest.raises(r.AnalystConfigError):
        r.run_analyst(env["settings"], _scored(), _State(), dry_run=True)


def test_rate_limit_marks_partial(env, monkeypatch):
    r = env["runner"]
    env["av"].rate_limit_at = "SAP.DEX"
    res = r.run_analyst(env["settings"], _scored(), _State(), now=NOW)
    assert res.status == "partial" and res.budget.av_blocked
    assert any(d.code == "av_rate_limit" for d in res.all_diagnostics())
    cg = next(c for c in res.companies if c.item.uid == "CG1")
    assert cg.events == []  # AV für restliche Titel übersprungen
    assert any(d.code == "trend_scan_no_news" for d in res.diagnostics)


def test_llm_budget_and_reserve(env):
    r = env["runner"]
    res = r.run_analyst(env["settings"], _scored(), _State(), max_llm_calls=5, now=NOW)
    assert res.status == "partial" and res.budget.exhausted
    # Reserve: Firmen-Stufe stoppt bei 5 − 3 = 2 Calls, Run-Ebene läuft noch.
    assert any(d.code == "llm_budget_exhausted" for d in res.diagnostics)
    assert res.budget.llm_calls <= 5


def test_llm_error_stops_llm_stage(env):
    r = env["runner"]
    env["llm"].fail_after = 1
    res = r.run_analyst(env["settings"], _scored(), _State(), now=NOW)
    assert res.status == "partial" and res.budget.llm_failed
    codes = [d.code for d in res.all_diagnostics()]
    assert "llm_error" in codes and "llm_skipped" in codes
    assert any(d.code == "trend_scan_skipped" for d in res.diagnostics)


def test_no_themes_no_trends_and_custom_scope(env):
    r = env["runner"]
    s = env["settings"]
    res = r.run_analyst(s, _scored(), _State(), scope="custom", tickers=["SAP", "NOPE"], no_themes=True, no_trends=True, now=NOW)
    assert [c.item.uid for c in res.companies] == ["SAP"]
    assert res.themes == [] and res.trends == []
    assert any(d.code == "custom_ticker_unknown" for d in res.diagnostics)
    from app.core.analyst import prompts

    schemas = [c["schema"] for c in env["llm"].calls]
    assert prompts.THEMES_SCHEMA not in schemas and prompts.TREND_SCHEMA not in schemas
    assert ("news", "technology") not in env["av"].calls


def test_company_exception_is_contained(env, monkeypatch):
    r = env["runner"]

    def boom(*a, **k):
        raise RuntimeError("kaputt")

    monkeypatch.setattr(r, "detect_interplay", boom)
    res = r.run_analyst(env["settings"], _scored(), _State(), now=NOW)
    assert all(any(d.code == "company_failed" for d in c.diagnostics) for c in res.companies)
    assert res.status in ("done", "partial")
