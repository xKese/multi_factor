"""Tests 1–3: Cache/TTL/no_data, Rate-Limiter, Feldmapping (Spec 14)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from app.backtest import av_client as avc


class _Resp:
    def __init__(self, payload, status=200, text=""):
        self._payload = payload
        self.status_code = status
        self.text = text

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _price_payload(days: int = 5) -> dict:
    block = {}
    for i in range(days):
        d = (pd.Timestamp("2024-01-02") + pd.Timedelta(days=i)).strftime("%Y-%m-%d")
        block[d] = {
            "1. open": "10", "2. high": "11", "3. low": "9", "4. close": str(10 + i),
            "5. adjusted close": str(9.5 + i), "6. volume": "1000", "7. dividend amount": "0",
            "8. split coefficient": "1.0",
        }
    return {"Meta Data": {"2. Symbol": "TEST"}, "Time Series (Daily)": block}


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "test-key-geheim")
    monkeypatch.setattr(avc.time, "sleep", lambda *_: None)


def _fake_get(responses):
    calls = []

    def get(url, params=None, timeout=None):
        calls.append(params)
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    get.calls = calls
    return get


def _client(tmp_path, **kw):
    cache = avc.BacktestCache(tmp_path / "cache")
    limiter = avc.TokenBucketLimiter(1000, clock=lambda: 0.0, sleep=lambda s: None)
    return avc.BacktestAVClient(cache, limiter=limiter, **kw), cache


def test_av_client_cache(tmp_path, monkeypatch, caplog):
    """Idempotenter Fetch, TTL, no_data-Cache, Key nie im Log (Test 1)."""
    note = _Resp({"Note": "Thank you for using Alpha Vantage! rate limit"})
    fake = _fake_get([note, _Resp(_price_payload())])
    monkeypatch.setattr(avc.requests, "get", fake)
    client, cache = _client(tmp_path, ttl_prices=7, ttl_no_data=30)

    with caplog.at_level(logging.DEBUG):
        df = client.fetch_prices("TEST")
    assert len(df) == 5 and list(df.columns) == ["close", "adj_close", "volume", "dividend", "split"]
    assert df.loc[pd.Timestamp("2024-01-03"), "adj_close"] == pytest.approx(10.5)
    assert len(fake.calls) == 2  # Rate-Limit-Note → Retry → Erfolg
    assert fake.calls[0]["apikey"] == "test-key-geheim"

    # Idempotent: zweiter Aufruf innerhalb der TTL → kein API-Call.
    df2 = client.fetch_prices("TEST")
    assert len(fake.calls) == 2
    pd.testing.assert_frame_equal(df, df2)
    entry = cache.entry(avc.EP_PRICES, "TEST")
    assert entry is not None and entry.status == avc.STATUS_OK and entry.rows == 5

    # TTL abgelaufen → erneut laden.
    cache.write(avc.EP_PRICES, "TEST", df, fetched_at=datetime.now() - timedelta(days=8))
    client.fetch_prices("TEST")
    assert len(fake.calls) == 3
    # --force lädt immer.
    client.fetch_prices("TEST", force=True)
    assert len(fake.calls) == 4

    # no_data: Antwort ohne erwartete Schlüssel wird gecacht (30 Tage).
    fake2 = _fake_get([_Resp({"Meta Data": {}})])
    monkeypatch.setattr(avc.requests, "get", fake2)
    assert client.fetch_prices("LEER") is None
    assert cache.entry(avc.EP_PRICES, "LEER").status == avc.STATUS_NO_DATA
    assert client.fetch_prices("LEER") is None
    assert len(fake2.calls) == 1
    cache.write(avc.EP_PRICES, "LEER", None, status=avc.STATUS_NO_DATA,
                fetched_at=datetime.now() - timedelta(days=31))
    client.fetch_prices("LEER")
    assert len(fake2.calls) == 2

    # Key erscheint weder im Log noch im Manifest/Cache-Verzeichnis.
    assert "test-key-geheim" not in caplog.text
    with sqlite3.connect(cache.root / "manifest.sqlite") as conn:
        dump = "\n".join(conn.iterdump())
    assert "test-key-geheim" not in dump
    for path in cache.root.rglob("*.parquet"):
        assert b"test-key-geheim" not in path.read_bytes()

    # Fehlender Key → Fehler ohne Request.
    monkeypatch.delenv("ALPHAVANTAGE_API_KEY")
    fake3 = _fake_get([_Resp(_price_payload())])
    monkeypatch.setattr(avc.requests, "get", fake3)
    with pytest.raises(avc.BacktestAVError) as err:
        client.fetch_prices("NEU")
    assert err.value.kind == "no_key" and not fake3.calls


def test_rate_limiter():
    """75 Aufrufe/min werden nicht überschritten (Test 2)."""
    now = [0.0]

    def clock():
        return now[0]

    def sleep(s):
        now[0] += s

    limiter = avc.TokenBucketLimiter(75, clock=clock, sleep=sleep)
    stamps = []
    for _ in range(300):
        limiter.acquire()
        stamps.append(now[0])
        now[0] += 0.05  # Aufrufer ist schneller als das Limit
    stamps = np.array(stamps)
    for t in stamps:
        window = ((stamps >= t) & (stamps < t + 60.0)).sum()
        assert window <= 75, f"{window} Aufrufe in 60 s ab {t}"
    # Kein Sleep-Raten: der Limiter nutzt das Budget aus (nicht pauschal 1 Sekunde).
    assert stamps[-1] < 300 * 1.0


def test_field_mapping():
    """Alle Mappings aus Spec 2.4 inkl. Fallbacks und "None" → NaN (Test 3)."""
    income = {
        "annualReports": [
            {"fiscalDateEnding": "2023-12-31", "totalRevenue": "1000", "costOfRevenue": "600",
             "ebit": "None", "operatingIncome": "150", "ebitda": "None",
             "depreciationAndAmortization": "50", "netIncome": "100", "interestExpense": "10"},
            {"fiscalDateEnding": "2022-12-31", "totalRevenue": "900", "costOfRevenue": "None",
             "ebit": "140", "operatingIncome": "130", "ebitda": "200",
             "depreciationAndAmortization": "45", "netIncome": "90", "interestExpense": "None"},
        ]
    }
    balance = {
        "annualReports": [
            {"fiscalDateEnding": "2023-12-31", "totalAssets": "2000", "totalLiabilities": "1200",
             "totalShareholderEquity": "800", "shortLongTermDebtTotal": "None", "longTermDebt": "300",
             "shortTermDebt": "50", "cashAndCashEquivalentsAtCarryingValue": "None",
             "cashAndShortTermInvestments": "120", "totalCurrentAssets": "500",
             "totalCurrentLiabilities": "300", "retainedEarnings": "400",
             "commonStockSharesOutstanding": "1000000"},
            {"fiscalDateEnding": "2022-12-31", "totalAssets": "1800", "totalLiabilities": "1100",
             "totalShareholderEquity": "700", "shortLongTermDebtTotal": "330", "longTermDebt": "None",
             "shortTermDebt": "None", "cashAndCashEquivalentsAtCarryingValue": "100",
             "cashAndShortTermInvestments": "150", "totalCurrentAssets": "450",
             "totalCurrentLiabilities": "280", "retainedEarnings": "350",
             "commonStockSharesOutstanding": "1000000"},
        ]
    }
    cashflow = {
        "annualReports": [
            {"fiscalDateEnding": "2023-12-31", "operatingCashflow": "180", "capitalExpenditures": "40"},
            {"fiscalDateEnding": "2022-12-31", "operatingCashflow": "160", "capitalExpenditures": "-30"},
        ]
    }
    inc = avc.map_income_statement(income).set_index("fiscal_date")
    bal = avc.map_balance_sheet(balance).set_index("fiscal_date")
    cf = avc.map_cash_flow(cashflow).set_index("fiscal_date")
    y23, y22 = pd.Timestamp("2023-12-31"), pd.Timestamp("2022-12-31")

    assert inc.loc[y23, "revenue"] == 1000 and inc.loc[y23, "cogs"] == 600
    assert inc.loc[y23, "ebit"] == 150  # Fallback operatingIncome
    assert inc.loc[y22, "ebit"] == 140  # ebit hat Vorrang
    assert inc.loc[y23, "ebitda"] == 150 + 50  # Fallback ebit + D&A
    assert inc.loc[y22, "ebitda"] == 200
    assert np.isnan(inc.loc[y22, "cogs"]) and np.isnan(inc.loc[y22, "interest_expense"])
    assert inc.loc[y23, "net_income"] == 100 and inc.loc[y23, "interest_expense"] == 10

    assert bal.loc[y23, "total_debt"] == 350  # LTD + STD
    assert bal.loc[y22, "total_debt"] == 330
    assert bal.loc[y23, "cash"] == 120  # Fallback cashAndShortTermInvestments
    assert bal.loc[y22, "cash"] == 100
    for col, val in (("total_assets", 2000), ("total_liab", 1200), ("equity", 800),
                     ("current_assets", 500), ("current_liab", 300), ("retained_earnings", 400),
                     ("shares_out", 1e6)):
        assert bal.loc[y23, col] == val

    assert cf.loc[y23, "fcf"] == 180 - 40 and cf.loc[y22, "fcf"] == 160 - 30

    merged = avc.merge_fundamentals(inc.reset_index(), bal.reset_index(), cf.reset_index())
    assert list(merged.columns) == ["fiscal_date", *avc.FUNDAMENTAL_COLUMNS]
    assert len(merged) == 2 and merged["fiscal_date"].is_monotonic_increasing

    ov = avc.map_overview({"Symbol": "X", "Name": "X Inc", "Sector": "TECHNOLOGY", "Industry": "None",
                           "Country": "USA", "AssetType": "Common Stock", "Exchange": "NYSE",
                           "Currency": "USD"}).iloc[0]
    assert ov["sector"] == "TECHNOLOGY" and ov["industry"] is None and ov["country"] == "USA"

    listing = avc.parse_listing_status(
        "symbol,name,exchange,assetType,ipoDate,delistingDate,status\n"
        "AAA,A Corp,NYSE,Stock,2001-01-02,null,Active\n"
        "BBB,B ETF,NYSE ARCA,ETF,2010-05-05,2020-01-01,Delisted\n"
    )
    assert list(listing["symbol"]) == ["AAA", "BBB"]
    assert pd.isna(listing.loc[0, "delisting_date"])
    assert listing.loc[1, "delisting_date"] == pd.Timestamp("2020-01-01")
