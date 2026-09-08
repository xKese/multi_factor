"""In-Memory-Datensatz des Backtests, strikt aus dem lokalen Cache gebaut.

Alle Kursreihen liegen als breite Panels (Zeilen = Handelstage des
Benchmarks, Spalten = Ticker) vor: ``close`` (USD, unadjustiert),
``adj_close`` (USD, Total Return), ``volume`` sowie ``adj_close_eur``.
Fundamentals sind je Ticker ein Frame mit ``fiscal_date`` und den internen
Feldnamen (Spec 2.4), ``overview`` ein Dict je Ticker, Listings je Stichtag
ein Frame. Die Verfügbarkeitsregel für Abschlüsse (Spec 4.1) lebt hier,
damit Universum und Snapshot dieselbe Punkt-in-Zeit-Sicht verwenden.

Der Datensatz kennt kein Netzwerk: ``from_cache`` liest ausschließlich
Parquet-Dateien; Tests bauen ihn direkt aus synthetischen Frames.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from . import av_client as avc
from .config import BacktestConfig
from .fx import FxSeries

log = logging.getLogger(__name__)


@dataclass
class BacktestDataset:
    calendar: pd.DatetimeIndex
    close: pd.DataFrame
    adj_close: pd.DataFrame
    volume: pd.DataFrame
    fx: FxSeries
    fundamentals: dict[str, pd.DataFrame] = field(default_factory=dict)
    overview: dict[str, dict] = field(default_factory=dict)
    listings: dict[date, pd.DataFrame] = field(default_factory=dict)
    delisted: pd.DataFrame = field(default_factory=pd.DataFrame)
    benchmark_ticker: str = "SPY"
    factors: pd.DataFrame | None = None
    cache_asof: object | None = None
    missing_prices: list[str] = field(default_factory=list)

    # ── Kurse ────────────────────────────────────────────────────────────

    @property
    def adj_close_eur(self) -> pd.DataFrame:
        cached = getattr(self, "_adj_eur", None)
        if cached is not None and cached.shape == self.adj_close.shape and cached.index.equals(
            self.adj_close.index
        ) and list(cached.columns) == list(self.adj_close.columns):
            return cached
        eur = self.fx.to_eur(self.adj_close)
        object.__setattr__(self, "_adj_eur", eur)
        return eur

    def benchmark_eur(self) -> pd.Series:
        if self.benchmark_ticker not in self.adj_close.columns:
            raise ValueError(f"Benchmark {self.benchmark_ticker!r} fehlt im Datensatz")
        return self.adj_close_eur[self.benchmark_ticker]

    def trading_days(self, start: date, end: date) -> pd.DatetimeIndex:
        idx = self.calendar
        return idx[(idx >= pd.Timestamp(start)) & (idx <= pd.Timestamp(end))]

    def last_price_date(self, ticker: str) -> pd.Timestamp | None:
        if ticker not in self.adj_close.columns:
            return None
        return self.adj_close[ticker].last_valid_index()

    def first_price_date(self, ticker: str) -> pd.Timestamp | None:
        if ticker not in self.adj_close.columns:
            return None
        return self.adj_close[ticker].first_valid_index()

    def delisting_date(self, ticker: str) -> pd.Timestamp | None:
        if self.delisted is None or self.delisted.empty or "symbol" not in self.delisted.columns:
            return None
        hit = self.delisted.loc[self.delisted["symbol"] == ticker, "delisting_date"]
        hit = hit.dropna()
        if hit.empty:
            return None
        return pd.Timestamp(hit.iloc[0])

    # ── Listings ─────────────────────────────────────────────────────────

    def listing_at(self, d: date) -> pd.DataFrame | None:
        """LISTING_STATUS zum Stichtag; fehlt der exakte Tag, der jüngste
        gecachte Stichtag ≤ d (nie ein späterer)."""
        if not self.listings:
            return None
        if d in self.listings:
            return self.listings[d]
        earlier = [k for k in self.listings if k <= d]
        if not earlier:
            return None
        return self.listings[max(earlier)]

    # ── Fundamentals (Spec 4.1) ──────────────────────────────────────────

    def fundamentals_at(
        self, ticker: str, d: date, lag_days: int, max_age_months: int
    ) -> tuple[pd.Series | None, pd.Series | None]:
        """(aktuell(d), vorjahr(d)) — der jüngste Jahresabschluss mit
        ``fiscal_date + lag_days ≤ d`` und der unmittelbar davor. ``None``,
        wenn keiner verfügbar ist oder der aktuelle älter als
        ``max_age_months`` Monate ist (dann gelten alle Fundamentals als NaN)."""
        fund = self.fundamentals.get(ticker)
        if fund is None or fund.empty:
            return None, None
        cutoff = pd.Timestamp(d) - pd.Timedelta(days=int(lag_days))
        avail = fund[fund["fiscal_date"] <= cutoff].sort_values("fiscal_date")
        if avail.empty:
            return None, None
        current = avail.iloc[-1]
        stale = pd.Timestamp(d) - pd.DateOffset(months=int(max_age_months))
        if pd.Timestamp(current["fiscal_date"]) < stale:
            return None, None
        prev = avail.iloc[-2] if len(avail) >= 2 else None
        return current, prev

    # ── Konstruktion ─────────────────────────────────────────────────────

    @classmethod
    def from_cache(
        cls,
        cache: avc.BacktestCache,
        config: BacktestConfig,
        tickers: list[str] | None = None,
    ) -> "BacktestDataset":
        """Baut den Datensatz aus dem Parquet-Cache (kein Netzwerk).

        ``tickers`` = None → alle Ticker mit gecachter Kursreihe.
        """
        bm = config.bt_benchmark_ticker
        bm_prices = cache.read(avc.EP_PRICES, bm)
        if bm_prices is None or bm_prices.empty:
            raise ValueError(
                f"Kein Kurscache für Benchmark {bm!r} — zuerst "
                "'python -m app.backtest fetch' ausführen."
            )
        fx_df = cache.read(avc.EP_FX, "USDEUR")
        if fx_df is None or fx_df.empty:
            raise ValueError("FX-Reihe USDEUR fehlt im Cache — zuerst fetch ausführen.")
        fx = FxSeries(fx_df["close"])

        calendar = pd.DatetimeIndex(bm_prices.index[bm_prices.index >= pd.Timestamp(config.bt_history_start)])
        if tickers is None:
            entries = cache.entries(avc.EP_PRICES)
            tickers = sorted(entries.loc[entries["status"] == avc.STATUS_OK, "ticker"])
        tickers = sorted(set(tickers) | {bm})

        close: dict[str, pd.Series] = {}
        adj: dict[str, pd.Series] = {}
        vol: dict[str, pd.Series] = {}
        missing: list[str] = []
        for t in tickers:
            df = bm_prices if t == bm else cache.read(avc.EP_PRICES, t)
            if df is None or df.empty:
                missing.append(t)
                continue
            df = df[~df.index.duplicated(keep="last")]
            close[t] = df["close"].reindex(calendar)
            adj[t] = df["adj_close"].reindex(calendar)
            vol[t] = df["volume"].reindex(calendar) if "volume" in df.columns else pd.Series(np.nan, index=calendar)

        fundamentals: dict[str, pd.DataFrame] = {}
        overview: dict[str, dict] = {}
        for t in tickers:
            if t == bm:
                continue
            inc = cache.read(avc.EP_INCOME, t)
            bal = cache.read(avc.EP_BALANCE, t)
            cf = cache.read(avc.EP_CASHFLOW, t)
            if any(x is not None for x in (inc, bal, cf)):
                fundamentals[t] = avc.merge_fundamentals(inc, bal, cf)
            ov = cache.read(avc.EP_OVERVIEW, t)
            if ov is not None and not ov.empty:
                overview[t] = ov.iloc[0].to_dict()

        listings: dict[date, pd.DataFrame] = {}
        delisted = pd.DataFrame()
        for _, row in cache.entries(avc.EP_LISTING).iterrows():
            if row["status"] != avc.STATUS_OK:
                continue
            key = str(row["ticker"])
            df = cache.read(avc.EP_LISTING, key)
            if df is None:
                continue
            if key.startswith("active_"):
                listings[date.fromisoformat(key[len("active_"):])] = df
            elif key == "delisted":
                delisted = df

        from .factor_regression import merge_factors

        factors = merge_factors(cache.read(avc.EP_FACTORS, "ff5"), cache.read(avc.EP_FACTORS, "mom"))

        return cls(
            calendar=calendar,
            close=pd.DataFrame(close, index=calendar),
            adj_close=pd.DataFrame(adj, index=calendar),
            volume=pd.DataFrame(vol, index=calendar),
            fx=fx,
            fundamentals=fundamentals,
            overview=overview,
            listings=listings,
            delisted=delisted,
            benchmark_ticker=bm,
            factors=factors,
            cache_asof=cache.latest_fetch(),
            missing_prices=missing,
        )
