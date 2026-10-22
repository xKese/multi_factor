"""Tests der KI-Chat-Werkzeuge (gespeicherte Daten + Alpha Vantage live, ohne Netzwerk)."""

from __future__ import annotations

import importlib
from datetime import date, datetime

import pandas as pd
import pytest

from app.core.config import Settings


def _scored() -> pd.DataFrame:
    return pd.DataFrame({
        "uid": ["SAP", "MBG", "SAN~sanofi", "SAN~banco-santander", "NOAV"],
        "ticker": ["SAP", "MBG", "SAN", "SAN", "NOAV"],
        "name": ["SAP SE", "Mercedes-Benz Group AG", "Sanofi", "Banco Santander", "No AV Corp"],
        "sector": ["IT", "Consumer", "Health", "Financials", "IT"],
        "industry": ["Software", "Autos", "Pharma", "Banks", "Software"],
        "industry_group": ["Software & Services", "Automobiles", "Pharma", "Banks", "Software & Services"],
        "region": ["EU", "EU", "EU", "EU", "US"],
        "filter_pass": [True, True, True, False, True],
        "zone_v2": ["KANDIDAT", "HALTEN", "KANDIDAT", "FILTER", "KANDIDAT"],
        "composite_score": [88.0, 70.0, 82.0, float("nan"), 80.0],
        "composite_z": [1.1, 0.2, 0.9, float("nan"), 0.8],
        "composite_pct": [0.9, 0.7, 0.85, 0.1, 0.8],
        "classification_v2": ["A", "B", "A", "D", "A"],
        "data_coverage_v2": [0.95] * 5,
        "z_value": [1.2, 0.1, 0.0, 0.3, 0.2],
        "z_quality": [0.5, 0.2, 0.0, 0.1, 0.1],
        "z_momentum": [-1.0, 0.3, 0.0, 0.1, 0.0],
        "z_investment": [0.2, 0.1, 0.0, 0.0, 0.0],
        "mom_12_1": [0.05, 0.1, 0.0, 0.0, 0.0],
        "market_cap": [250000.0, 60000.0, 120000.0, 70000.0, 9000.0],
        "pe": [25.0, 6.0, 14.0, 7.0, 30.0],
        "export_date": [date(2026, 10, 17)] * 5,
        "sma_signal": ["✓ GOLDEN CROSS"] * 5,
        "trend_phase": ["etabliert"] * 5,
        "trend_warning": [False] * 5,
        "total_score": [71.0, 55.0, 66.0, 40.0, 60.0],
        "last_price": [200.0, 60.0, 90.0, 4.0, 10.0],
        "sma_50": [190.0] * 5,
        "sma_200": [180.0] * 5,
    })


class _State:
    def portfolio_weights(self, pid=None):
        return {"MBG": 0.6, "SAP": 0.4}


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/chat.db")
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    from app.core import persistence
    from app.core.analyst import chat_tools as ct
    from app.core.analyst import store

    importlib.reload(persistence)
    importlib.reload(store)
    return ct


def _ctx(ct, **kw):
    kw.setdefault("av_enabled", False)
    return ct.ChatContext(settings=Settings(), scored=_scored(), state=_State(), today=date(2026, 10, 22), **kw)


def test_resolve_uid_orders_and_collisions(env):
    ct = env
    ctx = _ctx(ct)
    assert [h["uid"] for h in ct.resolve_uid(ctx, "SAP")] == ["SAP"]
    hits = ct.resolve_uid(ctx, "SAN")
    assert {h["uid"] for h in hits} == {"SAN~sanofi", "SAN~banco-santander"}
    assert [h["uid"] for h in ct.resolve_uid(ctx, "SAN~sanofi")] == ["SAN~sanofi"]
    assert [h["uid"] for h in ct.resolve_uid(ctx, "Mercedes")] == ["MBG"]
    assert [h["uid"] for h in ct.resolve_uid(ctx, "mercedes-benz group ag")] == ["MBG"]
    assert ct.resolve_uid(ctx, "xyz-nicht-da") == []
    out = ct.run_tool("resolve_company", {"query": "SAN"}, ctx)
    assert "SAN~sanofi" in out and "Banco Santander" in out
    assert "Parameter query fehlt" in ct.run_tool("resolve_company", {}, ctx)


def test_single_uses_page_context_and_reports_ambiguity(env):
    ct = env
    ctx = _ctx(ct, page_uid="MBG", use_page_context=True)
    out = ct.run_tool("company_snapshot", {}, ctx)
    assert out.startswith("**MBG — Mercedes-Benz Group AG**")
    assert "Bestand (aktives Portfolio): 60,0 %" in out
    assert "Alpha-Vantage-Symbol: nicht zugeordnet" in out
    out = ct.run_tool("company_snapshot", {"uid": "SAN"}, ctx)
    assert "mehrdeutig" in out and "SAN~banco-santander" in out
    ctx2 = _ctx(ct, page_uid="MBG", use_page_context=False)
    assert "Kein Titel angegeben" in ct.run_tool("company_snapshot", {}, ctx2)
    assert "nicht im Universum" in ct.run_tool("company_snapshot", {"uid": "ZZZ"}, ctx2)
    assert ctx.sources and "Koyfin-Import" in ctx.sources[0]


def test_screen_universe_filters_sorts_and_caps(env):
    ct = env
    ctx = _ctx(ct)
    out = ct.run_tool("screen_universe", {"region": "EU", "thresholds": {"z_value": {"min": 0.1}}, "columns": ["pe", "foo"], "n": 2}, ctx)
    assert "Screening: 2 von 3 Treffern (Universum 5 Titel)" in out
    assert "Unbekannte Spalten ignoriert: foo" in out
    lines = [ln for ln in out.splitlines() if ln.startswith("| ") and "SAP" in ln or "MBG" in ln]
    assert lines and "SAP" in lines[0]  # höchster Composite zuerst
    out = ct.run_tool("screen_universe", {"zone": "FILTER"}, ctx)
    assert "Banco Santander" in out and "SAP SE" not in out
    out = ct.run_tool("screen_universe", {"filter_pass": True, "sort_by": "pe", "ascending": True, "n": 1}, ctx)
    assert "Mercedes" in out  # niedrigstes KGV unter filter_pass
    out = ct.run_tool("screen_universe", {"sort_by": "unbekannt", "thresholds": {"pe": {"max": 0}}}, ctx)
    assert "Sortierspalte „unbekannt“ unbekannt" in out and "Keine Titel erfüllen" in out


def test_history_peers_and_overviews(env):
    ct = env
    ctx = _ctx(ct)
    assert "kein Verlauf archiviert" in ct.run_tool("company_history", {"uid": "SAP"}, ctx)
    out = ct.run_tool("company_peers", {"uid": "SAP", "n": 2}, ctx)
    assert "Peers von SAP" in out and "Distance" in out
    out = ct.run_tool("universe_overview", {"top_n": 2}, ctx)
    assert "Universum — Überblick" in out and "Kandidat" in out.lower() or "KANDIDAT" in out
    out = ct.run_tool("sector_overview", {}, ctx)
    assert "Sektor-Übersicht" in out and "Consumer" in out
    assert "Kein Archiv-Snapshot" in ct.run_tool("changes_since", {}, ctx)
    assert "Kein gespeichertes Modellportfolio" in ct.run_tool("model_portfolio_overview", {}, ctx)
    assert "Factor-Timing" in ct.run_tool("factor_timing_state", {}, ctx)
    assert "Kurscache" in ct.run_tool("risk_overview", {}, ctx)
    assert "Noch keine Analyst-Ergebnisse" in ct.run_tool("analyst_insights", {"uid": "SAP"}, ctx)
    assert "Noch kein Analyst-Lauf" in ct.run_tool("analyst_run_overview", {}, ctx)


def test_methodology_topics(env):
    ct = env
    ctx = _ctx(ct)
    out = ct.run_tool("methodology", {"topic": "zonen"}, ctx)
    assert "KANDIDAT" in out and "Aktuelle Parameter" not in out
    out = ct.run_tool("methodology", {"topic": "parameter"}, ctx)
    assert "Aktuelle Parameter" in out and "Faktorgewichte" in out
    out = ct.run_tool("methodology", {}, ctx)
    assert "Piotroski" in out and "Factor Timing" in out


def test_analyst_insights_reads_store(env):
    ct = env
    from app.core.analyst import store

    store.save_run({"run_id": "r1", "trigger": "cli", "scope": "portfolio", "model": "m", "status": "done", "dry_run": False, "started_at": datetime(2026, 10, 21)})
    store.save_insight({"run_id": "r1", "uid": "SAP", "ticker": "SAP", "kind": "synthesis", "period": "r1", "status": "ok", "model": "m",
                        "content": {"vorschlag": {"typ": "EINSTIEG_PRÜFEN", "konfidenz": 0.7, "begruendung": "Guidance angehoben"},
                                    "nicht_eingepreiste_themen": [{"thema": "Cloud-Marge"}]}, "n_calls": 1})
    store.save_suggestion({"run_id": "r1", "uid": "SAP", "ticker": "SAP", "suggestion_type": "EINSTIEG_PRÜFEN", "confidence": 0.7,
                           "rationale": "Guidance angehoben", "quant_zone": "KANDIDAT", "quant_score": 88.0, "quant_consistency": "bestaetigt"})
    ctx = _ctx(ct)
    out = ct.run_tool("analyst_insights", {"uid": "SAP"}, ctx)
    assert "Vorschläge" in out and "EINSTIEG_PRÜFEN" in out and "Cloud-Marge" in out
    out = ct.run_tool("analyst_run_overview", {}, ctx)
    assert "r1" in out and "Offene Vorschläge" in out


def test_run_tool_never_raises_and_truncates(env, monkeypatch):
    ct = env
    ctx = _ctx(ct, max_tool_chars=300)
    out = ct.run_tool("company_snapshot", {"uid": "SAP"}, ctx)
    assert len(out) <= 300 and ct.TRUNCATION_NOTE.strip() in out
    assert ctx.trace[-1]["truncated"] is True and ctx.trace[-1]["tool"] == "company_snapshot"
    assert "Unbekanntes Werkzeug" in ct.run_tool("nope", {}, ctx)

    def boom(args, c):
        raise RuntimeError("kaputt")

    monkeypatch.setitem(ct._BY_NAME, "company_peers", ct.ChatTool("company_peers", "", {}, boom))
    assert "fehlgeschlagen: RuntimeError: kaputt" in ct.run_tool("company_peers", {"uid": "SAP"}, ctx)
    assert ctx.n_tool_calls == 3


def test_tool_specs_exclude_live_without_key(env):
    ct = env
    names_all = {t["function"]["name"] for t in ct.tool_specs(include_live=True)}
    names_stored = {t["function"]["name"] for t in ct.tool_specs(include_live=False)}
    assert "av_news" in names_all and "av_news" not in names_stored
    assert "company_snapshot" in names_stored
    for spec in ct.tool_specs():
        assert spec["type"] == "function" and spec["function"]["parameters"]["type"] == "object"
    ctx = _ctx(ct, av_enabled=False)
    assert "Live-Daten sind nicht verfügbar" in ct.run_tool("av_news", {"uid": "SAP"}, ctx)


class _FakeAVF:
    def __init__(self):
        self.calls = []

    def fetch_news_sentiment(self, tickers=None, topics=None, time_from=None, limit=50, sort="LATEST", requests_per_minute=70):
        self.calls.append(("news", tickers, topics))
        return [{"title": "SAP hebt Guidance an", "url": "https://x/1", "time_published": datetime(2026, 10, 20, 9, 0), "summary": "s",
                 "source": "Reuters", "overall_sentiment_score": 0.31, "overall_sentiment_label": "Bullish",
                 "ticker_sentiment": [{"ticker": "SAP", "relevance_score": 0.9, "sentiment_score": 0.4, "sentiment_label": "Bullish"}], "topics": []}]

    def format_news_time(self, dt):
        return dt.strftime("%d.%m.%Y %H:%M")

    def fetch_earnings(self, symbol, requests_per_minute=70):
        self.calls.append(("earnings", symbol))
        return [{"fiscal_date_ending": date(2026, 6, 30), "reported_date": date(2026, 7, 28), "reported_eps": 1.5, "estimated_eps": 1.4, "surprise": 0.1, "surprise_pct": 7.1}]

    def latest_reported_quarter(self, rows):
        return ("2026Q2", date(2026, 7, 28))

    def fetch_transcript(self, symbol, quarter, requests_per_minute=70):
        self.calls.append(("transcript", symbol, quarter))
        if symbol == "MBG.DEX":
            return None
        return {"symbol": symbol, "quarter": quarter, "segments": [{"speaker": "CEO", "title": "CEO", "content": "Guidance raised.", "sentiment": 0.5}]}

    def fetch_insider_transactions(self, symbol, since=None, requests_per_minute=70):
        self.calls.append(("insider", symbol))
        return [{"transaction_date": date(2026, 10, 1), "executive": "A", "executive_title": "CFO", "security_type": "Common",
                 "acquisition_or_disposal": "A", "shares": 100.0, "share_price": 200.0}]


class _FakeAVC:
    class AlphaVantageError(RuntimeError):
        def __init__(self, msg, kind="error"):
            super().__init__(msg)
            self.kind = kind

    def __init__(self):
        self.calls = []

    def fetch_daily_adjusted(self, symbol, outputsize="full", requests_per_minute=70):
        self.calls.append(("prices", symbol, outputsize))
        idx = pd.date_range("2025-10-01", "2026-10-21", freq="B")
        vals = pd.Series(range(len(idx)), index=idx, dtype=float) + 100.0
        return pd.DataFrame({"adj_close": vals, "close": vals})

    def fetch_treasury_yield_10y(self, requests_per_minute=70):
        return pd.Series([4.0, 4.1], index=pd.to_datetime(["2026-10-20", "2026-10-21"]))

    def fetch_treasury_yield(self, maturity="10year", requests_per_minute=70):
        return pd.Series([3.5, 3.6], index=pd.to_datetime(["2026-10-20", "2026-10-21"]))

    def fetch_cpi(self, requests_per_minute=70):
        idx = pd.date_range("2025-01-01", periods=14, freq="MS")
        return pd.Series([300 + i for i in range(14)], index=idx, dtype=float)

    def fetch_wti(self, requests_per_minute=70):
        raise self.AlphaVantageError("rate", kind="rate_limit")

    def fetch_symbol_search(self, query, requests_per_minute=70):
        return [{"symbol": "SAP", "name": "SAP SE", "type": "Equity", "region": "Frankfurt", "currency": "EUR", "match_score": 0.9}]


@pytest.fixture()
def live(env, monkeypatch):
    ct = env
    avf, avc = _FakeAVF(), _FakeAVC()
    monkeypatch.setattr(ct, "_av_modules", lambda: (avc, avf))
    mapping = {"SAP": "SAP", "MBG": "MBG.DEX"}
    monkeypatch.setattr(ct, "_av_symbol_cached", lambda uid: mapping.get(uid))

    class _MD:
        @staticmethod
        def resolve_symbols(tickers, universe, settings, persist=True):
            return {}, list(tickers)

    monkeypatch.setattr("app.core.market_data.resolve_symbols", _MD.resolve_symbols)
    return ct, avf, avc


def test_live_tools_news_earnings_transcript_insider(live):
    ct, avf, avc = live
    ctx = _ctx(ct, av_enabled=True)
    out = ct.run_tool("av_news", {"uid": "SAP", "days": 5}, ctx)
    assert "[1]" in out and "https://x/1" in out and "Relevanz 0,90" in out
    assert any("https://x/1" in s for s in ctx.sources) and ctx.n_av_calls == 1
    out = ct.run_tool("av_news", {"topic": "technology"}, ctx)
    assert avf.calls[-1] == ("news", None, ["technology"])
    out = ct.run_tool("av_earnings", {"uid": "SAP"}, ctx)
    assert "Quartals-EPS SAP" in out and "7,10" in out
    out = ct.run_tool("av_transcript", {"uid": "SAP"}, ctx)
    assert "Guidance raised." in out and ("transcript", "SAP", "2026Q2") in avf.calls
    out = ct.run_tool("av_transcript", {"uid": "MBG", "quarter": "2026Q2"}, ctx)
    assert "Kein Transkript" in out
    out = ct.run_tool("av_insider", {"uid": "SAP"}, ctx)
    assert "1 Käufe, 0 Verkäufe" in out and "20.000" in out
    # Nicht auflösbar → Hinweis, kein Abruf
    out = ct.run_tool("av_earnings", {"uid": "NOAV"}, ctx)
    assert "nicht bei Alpha Vantage verfügbar" in out
    assert ("earnings", "NOAV") not in avf.calls


def test_live_prices_cache_and_macro(live, monkeypatch):
    ct, avf, avc = live
    from app.core import av_store

    saved = {}
    monkeypatch.setattr(av_store, "load_prices", lambda sym, until=None: saved.get(sym, pd.DataFrame(columns=["adj_close", "close"])))
    monkeypatch.setattr(av_store, "save_prices", lambda sym, df: saved.__setitem__(sym, df) or len(df))
    ctx = _ctx(ct, av_enabled=True)
    out = ct.run_tool("av_prices", {"uid": "SAP"}, ctx)
    assert "Kurse SAP (SAP) (aktualisiert)" in out and "Rendite 1M" in out and "Abstand SMA-200" in out
    assert avc.calls == [("prices", "SAP", "full")] and ctx.n_av_calls == 1
    # Cache frisch (letzter Kurs 21.10., heute 22.10.) → kein zweiter Abruf
    out = ct.run_tool("av_prices", {"uid": "SAP"}, ctx)
    assert "(aktualisiert)" not in out and len(avc.calls) == 1 and ctx.n_av_calls == 1
    out = ct.run_tool("av_macro", {"series": ["treasury_10y", "treasury_2y", "cpi", "wti", "foo"]}, ctx)
    assert "Zinsstruktur 10J–2J:** 0,50" in out and "Inflation (CPI, YoY):** 4,0 %" in out
    assert "Rate-Limit" in out and "Unbekannte Reihe „foo“" in out
    out = ct.run_tool("av_symbol_search", {"query": "SAP"}, ctx)
    assert "SAP SE" in out and "Frankfurt" in out


def test_av_error_kinds(live):
    ct, avf, avc = live
    err = avc.AlphaVantageError("x", kind="auth")
    assert "API-Key" in ct._av_error(err)
    assert "Rate-Limit" in ct._av_error(avc.AlphaVantageError("x", kind="rate_limit"))
    assert "Alpha Vantage: boom" == ct._av_error(RuntimeError("boom"))


def test_format_helpers(env):
    ct = env
    assert ct._fmt(None) == "–" and ct._fmt(float("nan")) == "–"
    assert ct._fmt(1234.5) == "1.234,50" and ct._fmt(3) == "3" and ct._fmt(True) == "ja"
    assert ct._fmt(date(2026, 1, 2)) == "2026-01-02" and ct._fmt(["a", 1]) == "a, 1"
    assert "| Ticker |" in ct._table([{"ticker": "SAP", "pe": 1.5}])
    assert "(keine Zeilen)" == ct._table([])
    rendered = ct._render({"n": 3, "rows": [{"a": 1}], "empty": [], "nested": {"x": None}})
    assert "- N: 3" in rendered and "| A |" in rendered and "Nested" in rendered and "Empty" not in rendered
