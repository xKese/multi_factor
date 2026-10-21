"""Tests für die AV-Fundamental-/News-Fetcher des KI-Analysten (ohne Netz)."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pytest

from app.core import av_client, av_fundamentals as avf

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200, text: str | None = None):
        self._payload = payload
        self.status_code = status_code
        self.text = text if text is not None else (
            json.dumps(payload) if isinstance(payload, dict) else ""
        )

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    monkeypatch.setattr(av_client.time, "sleep", lambda *_: None)
    av_client._limiter._last = 0.0


def _fake_get(responses: list):
    calls: list[dict] = []

    def get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    get.calls = calls
    return get


def test_fetch_earnings_normalises_and_sorts(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_earnings_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    rows = avf.fetch_earnings("TEST")

    assert fake.calls[0]["params"]["function"] == "EARNINGS"
    assert [r["fiscal_date_ending"] for r in rows] == [
        date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31)
    ]
    newest_reported = rows[1]
    assert newest_reported["reported_date"] == date(2026, 7, 28)
    assert newest_reported["surprise_pct"] == pytest.approx(4.5161)
    # "None"-Strings werden zu None, nicht zu Fehlern.
    assert rows[0]["reported_date"] is None and rows[0]["reported_eps"] is None
    assert avf.latest_reported_quarter(rows) == ("2026Q2", date(2026, 7, 28))


def test_fetch_earnings_unknown_symbol_is_empty(monkeypatch):
    monkeypatch.setattr(av_client.requests, "get", _fake_get([_FakeResponse({})]))
    assert avf.fetch_earnings("NOPE") == []
    assert avf.latest_reported_quarter([]) is None


def test_quarter_label():
    assert avf.quarter_label(date(2026, 1, 15)) == "2026Q1"
    assert avf.quarter_label(date(2026, 12, 31)) == "2026Q4"


def test_fetch_transcript_segments(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_transcript_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    tr = avf.fetch_transcript("TEST", "2026Q2")

    assert fake.calls[0]["params"]["quarter"] == "2026Q2"
    assert tr["quarter"] == "2026Q2"
    # Leeres Segment wird übersprungen.
    assert len(tr["segments"]) == 6
    assert tr["segments"][1]["title"] == "CEO"
    assert tr["segments"][1]["sentiment"] == pytest.approx(0.8)


def test_fetch_transcript_missing_returns_none(monkeypatch):
    monkeypatch.setattr(
        av_client.requests,
        "get",
        _fake_get([_FakeResponse({"symbol": "X", "quarter": "2026Q2", "transcript": []})]),
    )
    assert avf.fetch_transcript("X", "2026Q2") is None
    monkeypatch.setattr(av_client.requests, "get", _fake_get([_FakeResponse({})]))
    assert avf.fetch_transcript("X", "2026Q2") is None


def test_fetch_earnings_calendar_parses_csv(monkeypatch):
    body = (FIXTURES / "av_earnings_calendar_sample.csv").read_text(encoding="utf-8")
    fake = _fake_get([_FakeResponse(None, text=body)])
    monkeypatch.setattr(av_client.requests, "get", fake)

    rows = avf.fetch_earnings_calendar()

    assert fake.calls[0]["params"]["function"] == "EARNINGS_CALENDAR"
    assert "datatype" not in fake.calls[0]["params"]
    # Zeile ohne reportDate fällt weg; Sortierung nach Datum.
    assert [r["symbol"] for r in rows] == ["TEST", "OTHER"]
    assert rows[0]["report_date"] == date(2026, 10, 28)
    assert rows[0]["estimate"] == pytest.approx(1.70)
    assert rows[1]["estimate"] is None


def test_fetch_earnings_calendar_json_error_body(monkeypatch):
    fake = _fake_get([_FakeResponse(None, text='{"Error Message": "bad"}')])
    monkeypatch.setattr(av_client.requests, "get", fake)
    with pytest.raises(av_client.AlphaVantageError) as err:
        avf.fetch_earnings_calendar()
    assert err.value.kind == "error"


def test_fetch_earnings_calendar_rate_limit_then_ok(monkeypatch):
    body = (FIXTURES / "av_earnings_calendar_sample.csv").read_text(encoding="utf-8")
    fake = _fake_get(
        [
            _FakeResponse(None, text='{"Note": "limit"}'),
            _FakeResponse(None, text=body),
        ]
    )
    monkeypatch.setattr(av_client.requests, "get", fake)
    rows = avf.fetch_earnings_calendar(symbol="TEST")
    assert len(fake.calls) == 2
    assert fake.calls[0]["params"]["symbol"] == "TEST"
    assert rows


def test_fetch_news_sentiment_ticker_mode(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_news_sentiment_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    items = avf.fetch_news_sentiment(
        tickers=["TEST"], time_from=datetime(2026, 10, 1, 8, 30), limit=10
    )

    params = fake.calls[0]["params"]
    assert params["tickers"] == "TEST"
    assert params["time_from"] == "20261001T0830"
    assert params["limit"] == 10
    # Item ohne Titel fällt weg.
    assert len(items) == 2
    first = items[0]
    assert first["time_published"] == datetime(2026, 10, 20, 14, 30)
    assert first["overall_sentiment_score"] == pytest.approx(0.41)
    assert first["ticker_sentiment"][0]["ticker"] == "TEST"
    assert first["ticker_sentiment"][0]["relevance_score"] == pytest.approx(0.92)
    assert first["topics"][0]["topic"] == "Earnings"


def test_fetch_news_sentiment_topic_mode(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_news_topics_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    items = avf.fetch_news_sentiment(topics=["technology", "energy_transportation"])

    params = fake.calls[0]["params"]
    assert params["topics"] == "technology,energy_transportation"
    assert "tickers" not in params
    assert len(items) == 2  # Dedupe ist Sache des Aufrufers
    assert items[0]["topics"][0]["topic"] == "Energy & Transportation"


def test_fetch_news_sentiment_requires_filter():
    with pytest.raises(ValueError):
        avf.fetch_news_sentiment()


def test_fetch_insider_transactions_filter_since(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_insider_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    rows = avf.fetch_insider_transactions("TEST", since=date(2026, 9, 1))

    assert [r["transaction_date"] for r in rows] == [date(2026, 10, 15), date(2026, 9, 2)]
    assert rows[0]["acquisition_or_disposal"] == "A"
    assert rows[0]["shares"] == pytest.approx(5000)
    assert rows[1]["acquisition_or_disposal"] == "D"

    all_rows = avf.fetch_insider_transactions("TEST")
    # Zeile ohne Datum fällt weg.
    assert len(all_rows) == 3


def test_key_never_logged(monkeypatch, caplog):
    monkeypatch.setattr(av_client.requests, "get", _fake_get([_FakeResponse({})]))
    with caplog.at_level("DEBUG"):
        avf.fetch_earnings("TEST")
    assert "test-key" not in caplog.text
