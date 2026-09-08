"""Tests 4 und 8: FX-Umrechnung, Universumsausschlüsse."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.fx import FxSeries
from app.backtest.pit_builder import AlphaVantageSnapshotSource
from app.backtest.universe import build_universe, map_sector

from .conftest import make_dataset, small_config


def test_fx_conversion(dataset, config):
    """Beträge und Renditen in EUR; FX-Lookup nimmt letzten Kurs ≤ d (Test 4)."""
    fx = FxSeries(pd.Series({pd.Timestamp("2024-01-02"): 0.90, pd.Timestamp("2024-01-05"): 0.95}))
    assert fx.rate(date(2024, 1, 2)) == pytest.approx(0.90)
    assert fx.rate(date(2024, 1, 4)) == pytest.approx(0.90)  # Lücke → letzter Kurs ≤ d
    assert fx.rate(date(2024, 1, 5)) == pytest.approx(0.95)
    assert fx.rate(date(2024, 2, 1)) == pytest.approx(0.95)
    assert np.isnan(fx.rate(date(2023, 12, 31)))
    assert fx.amount_to_eur(100.0, date(2024, 1, 3)) == pytest.approx(90.0)
    usd = pd.Series([10.0, 20.0, 30.0], index=pd.to_datetime(["2024-01-02", "2024-01-04", "2024-01-05"]))
    eur = fx.to_eur(usd)
    assert list(eur.round(6)) == [9.0, 18.0, 28.5]

    # Snapshot: Kurs und Renditen aus der EUR-Reihe (Adjusted Close × FX je Tag).
    d = date(2014, 3, 31)
    src = AlphaVantageSnapshotSource(dataset, config)
    snap = src.build_snapshot(d).set_index("ticker")
    ts = pd.Timestamp(d)
    adj = dataset.adj_close["T00"]
    fx_d = dataset.fx.rate(d)
    assert snap.loc["T00", "last_price"] == pytest.approx(adj.loc[ts] * fx_d)
    eur_series = dataset.fx.to_eur(adj).loc[:ts]
    expected_ret_12m = eur_series.iloc[-1] / eur_series.iloc[-1 - 252] - 1.0
    assert snap.loc["T00", "ret_12m"] == pytest.approx(expected_ret_12m, rel=1e-6)
    usd_ret_12m = adj.loc[:ts].iloc[-1] / adj.loc[:ts].iloc[-1 - 252] - 1.0
    assert snap.loc["T00", "ret_12m"] != pytest.approx(usd_ret_12m, rel=1e-3)
    # Fundamentals in EUR Mio: net_income(USD) · fx / 1e6.
    cur, _ = dataset.fundamentals_at("T00", d, 90, 18)
    assert snap.loc["T00", "net_income"] == pytest.approx(cur["net_income"] * fx_d / 1e6)
    assert snap.loc["T00", "market_cap"] == pytest.approx(
        dataset.close.loc[ts, "T00"] * cur["shares_out"] * fx_d / 1e6
    )


def test_universe_exclusions():
    """ETFs, ADRs, Suffixe, Doppelgattungen, Mindesthistorie, Börse,
    Größenfilter, fehlende OVERVIEW (Test 8)."""
    ds = make_dataset(n=12)
    cfg = small_config(bt_min_market_cap=1000.0)
    d = date(2014, 3, 31)
    cal = ds.calendar

    def _add_price(ticker: str, like: str, start=None, scale: float = 1.0, vol_scale: float = 1.0):
        s = ds.adj_close[like] * scale
        c = ds.close[like] * scale
        v = ds.volume[like] * vol_scale
        if start is not None:
            s = s.where(cal >= pd.Timestamp(start))
            c = c.where(cal >= pd.Timestamp(start))
        ds.adj_close[ticker] = s
        ds.close[ticker] = c
        ds.volume[ticker] = v

    listing = ds.listings[max(k for k in ds.listings if k <= d)].copy()

    def _list(symbol, exchange="NYSE", asset_type="Stock"):
        nonlocal listing
        listing = pd.concat(
            [listing, pd.DataFrame([{"symbol": symbol, "name": symbol, "exchange": exchange,
                                     "asset_type": asset_type, "ipo_date": pd.Timestamp("2011-01-03"),
                                     "delisting_date": pd.NaT, "status": "Active"}])],
            ignore_index=True,
        )

    # ETF im Listing (assetType ETF) → raus.
    _add_price("ETF1", "T00"); _list("ETF1", asset_type="ETF")
    ds.overview["ETF1"] = {**ds.overview["T00"], "name": "Some ETF", "asset_type": "ETF"}
    # ADR (Country ≠ USA) → raus.
    _add_price("ADR1", "T01"); _list("ADR1")
    ds.overview["ADR1"] = {**ds.overview["T01"], "name": "Foreign Co", "country": "Germany"}
    # Preferred-Suffix → raus.
    _add_price("T02-P", "T02"); _list("T02-P")
    ds.overview["T02-P"] = {**ds.overview["T02"], "name": "Company 02 Pref"}
    # Doppelgattung: gleicher Name wie T03, weniger Volumen → nur T03 bleibt.
    _add_price("T03B", "T03", vol_scale=0.2); _list("T03B")
    ds.overview["T03B"] = {**ds.overview["T03"]}
    ds.fundamentals["T03B"] = ds.fundamentals["T03"].copy()
    # Doppelgattung, aber liquider als T04 → ersetzt T04.
    _add_price("T04B", "T04", vol_scale=5.0); _list("T04B")
    ds.overview["T04B"] = {**ds.overview["T04"]}
    ds.fundamentals["T04B"] = ds.fundamentals["T04"].copy()
    # Zu kurze Historie (< 250 Tage vor d) → raus.
    _add_price("NEU1", "T05", start="2013-10-01"); _list("NEU1")
    ds.overview["NEU1"] = {**ds.overview["T05"], "name": "New Listing"}
    ds.fundamentals["NEU1"] = ds.fundamentals["T05"].copy()
    # Falsche Börse → raus.
    _add_price("OTC1", "T06"); _list("OTC1", exchange="OTC")
    ds.overview["OTC1"] = {**ds.overview["T06"], "name": "Pink Sheet"}
    ds.fundamentals["OTC1"] = ds.fundamentals["T06"].copy()
    # Fehlende OVERVIEW → bleibt, Sektor Unknown.
    _add_price("NOOV", "T07"); _list("NOOV")
    ds.fundamentals["NOOV"] = ds.fundamentals["T07"].copy()
    # Small Cap (< 1.000 Mio EUR) → raus.
    _add_price("TINY", "T08", scale=0.01); _list("TINY")
    ds.overview["TINY"] = {**ds.overview["T08"], "name": "Tiny Corp"}
    ds.fundamentals["TINY"] = ds.fundamentals["T08"].copy()

    for k in list(ds.listings):
        ds.listings[k] = listing
    res = build_universe(d, ds, cfg)
    tickers = set(res.tickers)

    for excluded in ("ETF1", "ADR1", "T02-P", "T03B", "T04", "NEU1", "OTC1", "TINY", "SPY"):
        assert excluded not in tickers, excluded
    assert {"T03", "T04B", "NOOV"} <= tickers
    assert res.frame.loc["NOOV", "sector"] == "Unknown"
    assert res.frame.loc["NOOV", "overview_missing"]
    assert res.stats["missing_overview"] == 1
    assert res.stats["final"] == len(tickers)
    assert res.stats["listed_raw"] > res.stats["after_type_exchange"] > res.stats["after_suffix"]

    # Top-N greift nach dem Größenfilter.
    cfg_top = small_config(bt_min_market_cap=1000.0, bt_universe_top_n=5)
    res_top = build_universe(d, ds, cfg_top)
    assert len(res_top.tickers) == 5
    assert res_top.frame["market_cap"].min() >= res.frame["market_cap"].nlargest(5).min()

    assert map_sector("FINANCE") == "Financials"
    assert map_sector("REAL ESTATE & CONSTRUCTION") == "Real Estate"
    assert map_sector(None) == "Unknown"
