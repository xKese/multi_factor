"""Roundtrip-Tests für agent_analyses- und ticker_mappings-Persistenz."""

from __future__ import annotations

import importlib

from app.core import persistence


def _fresh_db(tmp_path, monkeypatch):
    """Persistenz-Modul auf eine frische SQLite-Datei zeigen lassen."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    importlib.reload(persistence)
    return persistence


def test_agent_analysis_roundtrip(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_agent_analysis(
        {
            "ticker": "MBG",
            "run_id": "MBG.F_20260723_120000",
            "agents_ticker": "MBG.F",
            "in_universe": True,
            "analysis_date": "2026-07-23",
            "rating": "Overweight",
            "executive_summary": "Kurzfassung",
            "reports": {"final_trade_decision": "**Rating**: Overweight"},
            "factor_context": {"total_score": 78.2, "classification": "B+"},
            "provider": "anthropic",
            "total_score": 78.2,
            "classification": "B+",
        }
    )

    loaded = p.load_agent_analysis("MBG")
    assert loaded is not None
    assert loaded["rating"] == "Overweight"
    assert loaded["agents_ticker"] == "MBG.F"
    assert loaded["reports"]["final_trade_decision"].startswith("**Rating**")
    assert loaded["factor_context"]["classification"] == "B+"
    assert loaded["in_universe"] == 1


def test_agent_ratings_latest_per_ticker(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    p.save_agent_analysis({"ticker": "AAA", "run_id": "r1", "rating": "Hold"})
    p.save_agent_analysis({"ticker": "AAA", "run_id": "r2", "rating": "Buy"})
    p.save_agent_analysis({"ticker": "BBB", "run_id": "r1", "rating": "Sell"})

    df = p.load_agent_ratings()
    ratings = dict(df[["ticker", "rating"]].itertuples(index=False))
    assert ratings["BBB"] == "Sell"
    # Beide AAA-Läufe teilen sich CURRENT_TIMESTAMP-Sekunde — es zählt genau
    # eine (die jüngste) Zeile pro Ticker.
    assert df["ticker"].value_counts()["AAA"] == 1

    hist = p.list_agent_analyses()
    assert len(hist) == 3


def test_ticker_mapping_roundtrip(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    assert p.load_ticker_mapping("MBG") is None
    p.save_ticker_mapping("MBG", "MBG.F", confirmed=True)
    assert p.load_ticker_mapping("MBG") == "MBG.F"

    # UPSERT ersetzt bestehendes Mapping.
    p.save_ticker_mapping("MBG", "MBG.DE", confirmed=True)
    assert p.load_ticker_mapping("MBG") == "MBG.DE"


def test_fail_open_without_engine(monkeypatch):
    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert persistence.load_agent_analysis("X") is None
    assert persistence.load_agent_ratings().empty
    assert persistence.list_agent_analyses().empty
    assert persistence.load_ticker_mapping("X") is None
