"""Tests für den Alpha-Vantage-Client (Parser, Fehlererkennung, Backoff)."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from app.core import av_client

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    """Kein echtes Sleep in Tests; API-Key gesetzt; Limiter zurückgesetzt."""

    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key")
    monkeypatch.setattr(av_client.time, "sleep", lambda *_: None)
    av_client._limiter._last = 0.0


def _fake_get(responses: list):
    """Liefert ein requests.get-Double, das die Antworten der Reihe nach
    ausgibt und alle Aufrufe protokolliert."""

    calls: list[dict] = []

    def get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    get.calls = calls
    return get


def test_fetch_daily_adjusted_parses_fixture(monkeypatch):
    fake = _fake_get([_FakeResponse(_load("av_daily_adjusted_sample.json"))])
    monkeypatch.setattr(av_client.requests, "get", fake)

    df = av_client.fetch_daily_adjusted("TEST")

    assert list(df.columns) == ["adj_close", "close"]
    # Aufsteigend sortiert, obwohl die API absteigend liefert.
    assert list(df.index) == sorted(df.index)
    assert df.loc[pd.Timestamp("2024-01-04"), "adj_close"] == pytest.approx(50.0)
    assert df.loc[pd.Timestamp("2024-01-08"), "close"] == pytest.approx(105.0)
    # Key wird mitgesendet, aber nie in die Assertions/Logs gezogen.
    assert fake.calls[0]["params"]["apikey"] == "test-key"
    assert fake.calls[0]["params"]["outputsize"] == "full"


def test_fetch_daily_adjusted_missing_adjusted_close_raises(monkeypatch):
    """Schema-Validierung: Antwort ohne '5. adjusted close' (z. B. der
    unadjustierte Endpunkt) muss als Parse-Fehler raisen, nicht raten."""

    payload = _load("av_daily_adjusted_sample.json")
    for row in payload["Time Series (Daily)"].values():
        row.pop("5. adjusted close")
    monkeypatch.setattr(
        av_client.requests, "get", _fake_get([_FakeResponse(payload)])
    )

    with pytest.raises(av_client.AlphaVantageError) as err:
        av_client.fetch_daily_adjusted("TEST")
    assert err.value.kind == "parse"
    assert "5. adjusted close" in str(err.value)


def test_fetch_fx_daily(monkeypatch):
    monkeypatch.setattr(
        av_client.requests,
        "get",
        _fake_get([_FakeResponse(_load("av_fx_daily_sample.json"))]),
    )

    series = av_client.fetch_fx_daily("USD", "EUR")

    assert series.loc[pd.Timestamp("2024-01-08")] == pytest.approx(0.913)
    assert list(series.index) == sorted(series.index)


def test_macro_series_dot_becomes_nan(monkeypatch):
    monkeypatch.setattr(
        av_client.requests,
        "get",
        _fake_get([_FakeResponse(_load("av_treasury_sample.json"))]),
    )

    series = av_client.fetch_treasury_yield_10y()

    assert pd.isna(series.loc[pd.Timestamp("2024-01-04")])
    assert series.loc[pd.Timestamp("2024-01-08")] == pytest.approx(4.02)


def test_fetch_wti(monkeypatch):
    monkeypatch.setattr(
        av_client.requests,
        "get",
        _fake_get([_FakeResponse(_load("av_wti_sample.json"))]),
    )

    series = av_client.fetch_wti()
    assert series.loc[pd.Timestamp("2024-01-05")] == pytest.approx(73.81)
    assert pd.isna(series.loc[pd.Timestamp("2024-01-04")])


def test_symbol_search_normalizes_keys(monkeypatch):
    monkeypatch.setattr(
        av_client.requests,
        "get",
        _fake_get([_FakeResponse(_load("av_symbol_search_sample.json"))]),
    )

    results = av_client.fetch_symbol_search("SAP")

    assert results[0]["symbol"] == "SAP"
    assert results[0]["currency"] == "USD"
    assert results[1] == {
        "symbol": "SAP.DEX",
        "name": "SAP SE",
        "type": "Equity",
        "region": "XETRA",
        "currency": "EUR",
        "match_score": pytest.approx(0.6667),
    }


def test_rate_limit_body_retries_then_raises(monkeypatch):
    """Limit-Antworten (HTTP 200 + nur 'Note') werden retried; bleibt das
    Limit bestehen, raist der Client mit kind='rate_limit'."""

    note = _FakeResponse(_load("av_rate_limit_note.json"))
    fake = _fake_get([note, note, note, note])
    monkeypatch.setattr(av_client.requests, "get", fake)

    with pytest.raises(av_client.AlphaVantageError) as err:
        av_client.fetch_wti()

    assert err.value.kind == "rate_limit"
    assert len(fake.calls) == len(av_client.RETRY_DELAYS) + 1


def test_rate_limit_recovers_after_retry(monkeypatch):
    note = _FakeResponse(_load("av_rate_limit_note.json"))
    ok = _FakeResponse(_load("av_wti_sample.json"))
    fake = _fake_get([note, ok])
    monkeypatch.setattr(av_client.requests, "get", fake)

    series = av_client.fetch_wti()
    assert len(series) == 4
    assert len(fake.calls) == 2


def test_error_message_raises_without_retry(monkeypatch):
    fake = _fake_get(
        [_FakeResponse({"Error Message": "Invalid API call for symbol XXXX"})]
    )
    monkeypatch.setattr(av_client.requests, "get", fake)

    with pytest.raises(av_client.AlphaVantageError) as err:
        av_client.fetch_daily_adjusted("XXXX")

    assert err.value.kind == "error"
    assert len(fake.calls) == 1


def test_missing_api_key_raises(monkeypatch):
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY", raising=False)
    called = _fake_get([_FakeResponse({})])
    monkeypatch.setattr(av_client.requests, "get", called)

    with pytest.raises(av_client.AlphaVantageError) as err:
        av_client.fetch_wti()

    assert "ALPHAVANTAGE_API_KEY" in str(err.value)
    assert not called.calls
