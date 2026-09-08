"""Synthetische Fixtures für die Backtest-Tests (kein Netzwerk, Spec 14)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.config import BacktestConfig
from app.backtest.dataset import BacktestDataset
from app.backtest.fx import FxSeries

SECTORS = [
    "Information Technology", "Financials", "Health Care", "Industrials",
    "Consumer Discretionary", "Energy", "Real Estate",
]


def make_dataset(
    n: int = 30,
    start: date = date(2011, 1, 3),
    end: date = date(2015, 12, 31),
    seed: int = 7,
    with_listings: bool = True,
    fiscal_years: tuple[int, ...] = (2010, 2011, 2012, 2013, 2014, 2015),
) -> BacktestDataset:
    rng = np.random.default_rng(seed)
    calendar = pd.bdate_range(start, end)
    tickers = [f"T{i:02d}" for i in range(n)]
    n_days = len(calendar)

    # Benchmark + Titel: geometrische Random Walks mit titelspezifischer Drift.
    bm_ret = rng.normal(0.0004, 0.01, n_days)
    bm = 100.0 * np.cumprod(1.0 + bm_ret)
    close = {"SPY": bm}
    for i, t in enumerate(tickers):
        beta = 0.6 + 0.8 * rng.random()
        drift = rng.normal(0.0003, 0.0004)
        idio = rng.normal(0.0, 0.012 + 0.01 * (i % 4) / 4, n_days)
        rets = drift + beta * bm_ret + idio
        close[t] = float(20 + 80 * rng.random()) * np.cumprod(1.0 + rets)
    close_df = pd.DataFrame(close, index=calendar)
    # Adjusted Close: Dividenden 2 % p. a. → Total Return liegt leicht über Close.
    adj_df = close_df * np.exp(np.linspace(0.0, 0.02 * n_days / 252, n_days))[:, None]
    adj_df["SPY"] = close_df["SPY"]
    volume = pd.DataFrame(
        rng.integers(500_000, 5_000_000, size=(n_days, n + 1)).astype(float),
        index=calendar,
        columns=["SPY", *tickers],
    )
    fx = FxSeries(pd.Series(0.85 + 0.05 * np.sin(np.arange(n_days) / 90.0), index=calendar))

    fundamentals: dict[str, pd.DataFrame] = {}
    overview: dict[str, dict] = {}
    for i, t in enumerate(tickers):
        shares = 4e8 + 1e8 * (i % 5)
        base_rev = 8e9 + 4e9 * rng.random()
        growth = 1.02 + 0.08 * rng.random()
        margin = 0.10 + 0.12 * rng.random()
        rows = []
        # Piotroski-freundliche Bilanzdynamik: steigende Margen, sinkende
        # Verschuldung, stabile Aktienzahl, steigender Kapitalumschlag —
        # jeder dritte Titel bewusst schwächer (fällt am Filter).
        weak = i % 3 == 2
        for k, year in enumerate(fiscal_years):
            rev = base_rev * growth**k
            trend = -0.02 * k if weak else 0.01 * k
            ebit = rev * (margin + trend) * (1 + 0.03 * rng.normal())
            rows.append(
                {
                    "fiscal_date": pd.Timestamp(year, 12, 31),
                    "revenue": rev,
                    "cogs": rev * (0.60 - 0.004 * k if not weak else 0.60 + 0.01 * k),
                    "ebit": ebit,
                    "ebitda": ebit * 1.3,
                    "net_income": ebit * (0.7 if not weak else 0.5),
                    "interest_expense": abs(ebit) * 0.08 + 1e6,
                    "total_assets": rev * 1.2 * (1 - 0.01 * k if not weak else 1 + 0.06 * k),
                    "total_liab": rev * 0.7,
                    "equity": rev * 0.5 * (1 + 0.03 * k),
                    "total_debt": rev * 0.3 * (1 - 0.03 * k if not weak else 1 + 0.05 * k),
                    "cash": rev * 0.1,
                    "current_assets": rev * (0.40 + 0.01 * k),
                    "current_liab": rev * 0.25,
                    "retained_earnings": rev * 0.3,
                    "shares_out": shares * (1.0 if not weak else 1 + 0.02 * k),
                    "ocf": ebit * (0.9 if not weak else 0.4),
                    "capex": -rev * 0.05,
                    "fcf": ebit * (0.9 if not weak else 0.4) - rev * 0.05,
                }
            )
        fundamentals[t] = pd.DataFrame(rows)
        overview[t] = {
            "symbol": t,
            "name": f"Company {i:02d}",
            "sector": SECTORS[i % len(SECTORS)],
            "industry": f"Industry {i % 3}",
            "country": "USA",
            "asset_type": "Common Stock",
            "exchange": "NYSE",
            "currency": "USD",
        }

    listings: dict[date, pd.DataFrame] = {}
    if with_listings:
        frame = pd.DataFrame(
            {
                "symbol": ["SPY", *tickers],
                "name": ["SPDR S&P 500", *[f"Company {i:02d}" for i in range(n)]],
                "exchange": "NYSE",
                "asset_type": ["ETF", *["Stock"] * n],
                "ipo_date": pd.Timestamp(start),
                "delisting_date": pd.NaT,
                "status": "Active",
            }
        )
        for d in _rebalance_dates(calendar):
            listings[d] = frame.copy()

    return BacktestDataset(
        calendar=calendar,
        close=close_df,
        adj_close=adj_df,
        volume=volume,
        fx=fx,
        fundamentals=fundamentals,
        overview=overview,
        listings=listings,
        delisted=pd.DataFrame(columns=["symbol", "delisting_date"]),
        benchmark_ticker="SPY",
    )


def _rebalance_dates(calendar: pd.DatetimeIndex) -> list[date]:
    out = []
    for (year, month), idx in pd.Series(calendar, index=calendar).groupby(
        [calendar.year, calendar.month]
    ):
        if month in (3, 6, 9, 12):
            out.append(idx.iloc[-1].date())
    return out


def small_config(**overrides) -> BacktestConfig:
    cfg = BacktestConfig(
        name="test",
        bt_start=date(2013, 3, 29),
        bt_end=date(2015, 12, 31),
        bt_history_start=date(2011, 1, 3),
        bt_min_market_cap=100.0,
        bt_universe_top_n=1000,
        bt_min_history_days=250,
        settings_overrides={
            "pc_target_n": 8,
            "pc_min_n": 5,
            "pc_max_n": 10,
            "pc_weight_cap": 0.25,
            "pc_weight_floor": 0.05,
            "pc_sector_band": 0.5,
            "pc_region_band": 0.5,
            "pc_max_per_sector": 5,
            "pc_te_max": 0.5,
            "pc_te_target_low": 0.0,
            "pc_te_target_high": 0.5,
            "pc_max_cte_share": 0.9,
            "filter_min_market_cap": 100.0,
            "filter_min_adv": 0.0,
            "pc_turnover_budget_full": 1.0,
            "pc_turnover_budget_interim": 1.0,
        },
    )
    for key, value in overrides.items():
        if key == "settings_overrides":
            cfg.settings_overrides.update(value)
        else:
            setattr(cfg, key, value)
    return cfg


@pytest.fixture
def dataset() -> BacktestDataset:
    return make_dataset()


@pytest.fixture
def config() -> BacktestConfig:
    return small_config()
