"""Tests der Ereignis-Erkennung und des Markt-Reaktions-Blocks."""

from __future__ import annotations

from datetime import date, datetime

import pandas as pd
import pytest

from app.core.analyst import events as ev

TODAY = date(2026, 10, 21)


def _earnings():
    return [
        {"fiscal_date_ending": date(2026, 6, 30), "reported_date": date(2026, 7, 28),
         "reported_eps": 1.62, "estimated_eps": 1.55, "surprise": 0.07, "surprise_pct": 4.5},
        {"fiscal_date_ending": date(2026, 3, 31), "reported_date": date(2026, 4, 29),
         "reported_eps": 1.4, "estimated_eps": 1.45, "surprise": -0.05, "surprise_pct": -3.4},
        {"fiscal_date_ending": date(2026, 9, 30), "reported_date": None},
    ]


def test_earnings_event_first_run_and_cursor():
    e = ev.earnings_event(None, _earnings(), "SAP", "SAP", TODAY)
    assert e.kind == "earnings" and e.period == "2026-07-28"
    assert e.dedupe_key == "SAP|earnings|2026-07-28"
    assert "über Erwartung" in e.title and "4,5 %" in e.title
    assert e.payload["surprise_pct"] == 4.5
    # Cursor auf dem neuesten Bericht → nichts Neues.
    assert ev.earnings_event({"last_earnings_report_date": date(2026, 7, 28)}, _earnings(), "SAP", "SAP", TODAY) is None
    # Älterer Cursor → neuester Bericht.
    assert ev.earnings_event({"last_earnings_report_date": date(2026, 5, 1)}, _earnings(), "SAP", "SAP", TODAY).period == "2026-07-28"
    # Erstlauf: Berichte älter als 120 Tage zählen nicht.
    assert ev.earnings_event(None, _earnings(), "SAP", "SAP", date(2027, 3, 1)) is None


def test_upcoming_event_window():
    rows = [
        {"symbol": "SAP", "report_date": date(2026, 10, 28), "estimate": 1.7, "fiscal_date_ending": date(2026, 9, 30)},
        {"symbol": "SAP", "report_date": date(2027, 1, 28), "estimate": None},
    ]
    e = ev.upcoming_event(rows, "SAP", "SAP", TODAY)
    assert e.kind == "earnings_upcoming" and "in 7 Tagen" in e.title and "1,70" in e.title
    assert ev.upcoming_event(rows, "SAP", "SAP", date(2026, 12, 1)) is None


def test_transcript_needed():
    assert ev.transcript_needed(None, "2026Q2", lambda q: False) is True
    assert ev.transcript_needed({"last_transcript_quarter": "2026Q2"}, "2026Q2", lambda q: False) is False
    assert ev.transcript_needed({"last_transcript_quarter": "2026Q1"}, "2026Q2", lambda q: True) is False
    assert ev.transcript_needed(None, None, lambda q: False) is False
    t = ev.transcript_event({"quarter": "2026Q2", "segments": [{"content": "a" * 1500}]}, "SAP", "SAP", TODAY)
    assert t.period == "2026Q2" and t.payload["n_segments"] == 1


def test_insider_event_aggregates():
    rows = [
        {"transaction_date": date(2026, 10, 15), "acquisition_or_disposal": "A", "shares": 5000, "executive": "CEO"},
        {"transaction_date": date(2026, 9, 2), "acquisition_or_disposal": "D", "shares": 2000},
        {"transaction_date": date(2026, 5, 20), "acquisition_or_disposal": "D", "shares": 1000},
    ]
    e = ev.insider_event(None, rows, "SAP", "SAP", TODAY)
    assert e.payload["n_buys"] == 1 and e.payload["n_sells"] == 1
    assert e.payload["net_shares"] == 3000
    assert e.period == "2026-10-15"
    assert ev.insider_event({"last_insider_date": date(2026, 10, 15)}, rows, "SAP", "SAP", TODAY) is None
    assert ev.insider_event({"last_insider_date": date(2026, 10, 1)}, rows, "SAP", "SAP", TODAY).payload["n_sells"] == 0


def _feed():
    return [
        {"title": "A", "url": "u1", "time_published": datetime(2026, 10, 20, 14, 30), "summary": "s", "source": "x",
         "ticker_sentiment": [{"ticker": "SAP", "relevance_score": 0.9, "sentiment_score": 0.5}]},
        {"title": "B", "url": "u2", "time_published": datetime(2026, 10, 18, 9, 0), "summary": "s", "source": "x",
         "ticker_sentiment": [{"ticker": "SAP", "relevance_score": 0.1, "sentiment_score": 0.0}]},
        {"title": "C", "url": "u3", "time_published": datetime(2026, 10, 10, 9, 0), "summary": "s", "source": "x",
         "ticker_sentiment": [{"ticker": "SAP", "relevance_score": 0.8, "sentiment_score": -0.3}]},
    ]


def test_news_event_relevance_and_cursor():
    e = ev.news_event(None, _feed(), "SAP", "SAP", "SAP", 0.3)
    assert e.payload["n_items"] == 2  # B fällt wegen Relevanz weg
    assert e.period == "2026-10-20T14:30"
    assert e.payload["mean_sentiment"] == pytest.approx(0.1)
    e2 = ev.news_event({"last_news_at": datetime(2026, 10, 15)}, _feed(), "SAP", "SAP", "SAP", 0.3)
    assert e2.payload["n_items"] == 1
    assert ev.news_event({"last_news_at": datetime(2026, 10, 21)}, _feed(), "SAP", "SAP", "SAP", 0.3) is None


def test_score_events():
    row = pd.Series({"zone_v2": "KANDIDAT", "composite_score": 85.0})
    assert ev.score_events(None, row, "SAP", "SAP", TODAY) == []
    out = ev.score_events({"last_zone": "HALTEN", "last_score": 70.0}, row, "SAP", "SAP", TODAY)
    assert [e.kind for e in out] == ["zone", "score"]
    assert out[0].title == "Zone HALTEN → KANDIDAT"
    assert out[1].payload["delta"] == pytest.approx(15.0)
    assert ev.score_events({"last_zone": "KANDIDAT", "last_score": 80.0}, row, "SAP", "SAP", TODAY) == []


def test_build_market_reaction():
    idx = pd.to_datetime(["2026-07-27", "2026-07-28", "2026-08-15", "2026-10-20"])
    prices = pd.DataFrame({"adj_close": [100.0, 102.0, 110.0, 99.0]}, index=idx)
    row = pd.Series({"ret_1m": -0.05, "mom_12_1": 0.2, "z_eps_revisions_3m": float("nan")})
    news = [
        {"time_published": datetime(2026, 10, 1), "ticker_sentiment_score": -0.2},
        {"time_published": datetime(2026, 10, 10), "ticker_sentiment_score": 0.4},
        {"time_published": datetime(2026, 10, 20), "ticker_sentiment_score": 0.6},
    ]
    mr = ev.build_market_reaction(row, prices, news, date(2026, 7, 28))
    assert mr["ret_since_report"] == pytest.approx(99 / 102 - 1, abs=1e-4)
    assert mr["ret_1m"] == -0.05 and "z_eps_revisions_3m" not in mr
    assert mr["news_sentiment_earlier"] == pytest.approx(-0.2)
    assert mr["news_sentiment_recent"] == pytest.approx(0.5)
    assert mr["n_news"] == 3
    assert ev.build_market_reaction(row, None, [], None) == {"ret_1m": -0.05, "mom_12_1": 0.2, "n_news": 0}
