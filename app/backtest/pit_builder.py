"""Punkt-in-Zeit-Snapshot je Stichtag im Koyfin-Spaltenschema (Spec 4).

``AlphaVantageSnapshotSource.build_snapshot(d)`` erzeugt einen DataFrame mit
exakt den Spalten, die der produktive Import liefert (``KOYFIN_COLUMNS`` +
``OPTIONAL_COLUMNS`` + ``uid``). Der Frame geht anschließend unverändert
durch ``compute_scores`` (v1, liefert Piotroski) und ``compute_scores_v2``
— keine Kennzahl des Modells wird hier nachgebaut, nur die Rohspalten des
Exports werden aus Kursen und Jahresabschlüssen hergestellt.

Strikt ohne Blick nach vorn: Kurse werden auf ``≤ d`` geschnitten,
Abschlüsse gelten erst ``fiscal_date + bt_reporting_lag_days`` (Spec 4.1),
das Listing ist das des Stichtags. OVERVIEW (Sektor, Name) ist aktuell,
nicht historisch — dokumentierter Bias.
"""

from __future__ import annotations

import io
from abc import ABC, abstractmethod
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from app.core import portfolio_construction as pc
from app.core.config import V2_CLEAN_BOUNDS
from app.core.schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS
from app.core.uid import assign_uids

from .config import BacktestConfig
from .dataset import BacktestDataset
from .universe import SECTOR_UNKNOWN, UniverseResult, build_universe

REGION_US = "United States"
TRADING_DAYS = 252
_RET_OFFSETS = {"ret_1m": 21, "ret_3m": 63, "ret_6m": 126, "ret_12m": 252}

SNAPSHOT_COLUMNS: tuple[str, ...] = tuple(KOYFIN_COLUMNS) + tuple(OPTIONAL_COLUMNS)

# Rohspalten, die der Backtest aus Fundamentals belegt (EUR Mio) — der Rest
# der Koyfin-Spalten (ps, peg, div_yield, CAGRs, Forward-Wachstum, sma_20,
# avg_volume) bleibt NaN: nicht verfügbar bzw. nur v1-relevant.
_FUND_CURRENT = ("net_income", "ocf", "total_assets", "total_debt", "current_assets",
                 "current_liab", "shares_out", "revenue", "cogs")


class SnapshotSource(ABC):
    """Schnittstelle für Snapshot-Quellen (Spec 16): zweite Implementierung
    z. B. auf Bloomberg-Daten ohne Änderung an Simulator/Report."""

    @abstractmethod
    def build_snapshot(self, d: date) -> pd.DataFrame:
        """Koyfin-kompatibler Frame zum Stichtag ``d`` (inkl. ``uid``)."""

    @abstractmethod
    def universe_stats(self, d: date) -> dict[str, int]:
        """Zählstatistik des Universums zum Stichtag (für Report/Diagnose)."""


def _safe_ratio(num: pd.Series, den: pd.Series, den_positive: bool = True) -> pd.Series:
    if den_positive:
        return (num / den).where(den > 0)
    return (num / den).where(den != 0)


def _returns_block(adj_eur: pd.DataFrame, bm_eur: pd.Series) -> pd.DataFrame:
    """Kurskennzahlen aus EUR-Adjusted-Close (Fenster ≤ d, Spalten = Ticker)."""
    # Kurslücken innerhalb des Fensters kurz fortschreiben, damit ein
    # einzelner fehlender Tag keine 12M-Rendite vernichtet.
    px = adj_eur.ffill(limit=5)
    last = px.iloc[-1]
    out = pd.DataFrame(index=px.columns)
    out["last_price"] = last
    n = len(px)
    for name, off in _RET_OFFSETS.items():
        if n > off:
            out[name] = last / px.iloc[-1 - off] - 1.0
        else:
            out[name] = np.nan
    win = px.tail(TRADING_DAYS + 1)
    logret = np.log(win / win.shift(1))
    out["volatility_1y"] = logret.std(ddof=1) * np.sqrt(TRADING_DAYS)
    out.loc[logret.count() < int(TRADING_DAYS * 0.8), "volatility_1y"] = np.nan
    bm_win = bm_eur.reindex(win.index).ffill(limit=5)
    bm_ret = np.log(bm_win / bm_win.shift(1))
    simple = win.pct_change(fill_method=None)
    bm_simple = bm_win.pct_change(fill_method=None)
    var_bm = float(bm_simple.var(ddof=1)) if bm_simple.count() > 30 else np.nan
    if np.isfinite(var_bm) and var_bm > 0:
        cov = simple.apply(lambda s: s.cov(bm_simple))
        out["beta"] = (cov / var_bm).where(simple.count() >= int(TRADING_DAYS * 0.8))
    else:
        out["beta"] = np.nan
    out["high_52w"] = win.max()
    out["low_52w"] = win.min()
    out["sma_50"] = px.tail(50).mean().where(px.tail(50).count() >= 40)
    out["sma_200"] = px.tail(200).mean().where(px.tail(200).count() >= 160)
    del bm_ret
    return out


class AlphaVantageSnapshotSource(SnapshotSource):
    """Snapshot aus dem Alpha-Vantage-Datensatz (Spec 4.3)."""

    def __init__(self, dataset: BacktestDataset, config: BacktestConfig) -> None:
        self.dataset = dataset
        self.config = config
        self._last_universe: tuple[date, UniverseResult] | None = None

    # ── Universum ────────────────────────────────────────────────────────

    def universe(self, d: date) -> UniverseResult:
        if self._last_universe is not None and self._last_universe[0] == d:
            return self._last_universe[1]
        result = build_universe(d, self.dataset, self.config)
        self._last_universe = (d, result)
        return result

    def universe_stats(self, d: date) -> dict[str, int]:
        return dict(self.universe(d).stats)

    # ── Fundamentals ─────────────────────────────────────────────────────

    def _fundamentals(self, tickers: list[str], d: date) -> tuple[pd.DataFrame, pd.DataFrame]:
        cfg = self.config
        cur_rows: dict[str, pd.Series] = {}
        prev_rows: dict[str, pd.Series] = {}
        for t in tickers:
            cur, prev = self.dataset.fundamentals_at(
                t, d, cfg.bt_reporting_lag_days, cfg.bt_fundamentals_max_age_months
            )
            if cur is not None:
                cur_rows[t] = cur
            if prev is not None:
                prev_rows[t] = prev
        cols = [c for c in (self.dataset.fundamentals or {}).get(next(iter(self.dataset.fundamentals), ""), pd.DataFrame()).columns] if self.dataset.fundamentals else []
        cur_df = pd.DataFrame(cur_rows).T if cur_rows else pd.DataFrame(columns=cols)
        prev_df = pd.DataFrame(prev_rows).T if prev_rows else pd.DataFrame(columns=cols)
        cur_df = cur_df.reindex(tickers)
        prev_df = prev_df.reindex(tickers)
        for df in (cur_df, prev_df):
            for c in df.columns:
                if c != "fiscal_date":
                    df[c] = pd.to_numeric(df[c], errors="coerce")
        return cur_df, prev_df

    # ── Snapshot ─────────────────────────────────────────────────────────

    def build_snapshot(self, d: date) -> pd.DataFrame:
        cfg = self.config
        ds = self.dataset
        ts = pd.Timestamp(d)
        uni = self.universe(d)
        tickers = uni.tickers
        fx = ds.fx.rate(d)
        scale = fx / 1e6  # USD → EUR Mio

        snap = pd.DataFrame(index=pd.Index(tickers, name="_ticker"))
        snap["ticker"] = tickers
        snap["name"] = uni.frame["name"].reindex(tickers).fillna(pd.Series(tickers, index=tickers))
        snap["sector"] = uni.frame["sector"].reindex(tickers).fillna(SECTOR_UNKNOWN)
        snap["industry"] = uni.frame["industry"].reindex(tickers).fillna(SECTOR_UNKNOWN)
        snap["region"] = REGION_US

        # Kurse in EUR (≤ d).
        cols = [t for t in tickers if t in ds.adj_close.columns]
        adj_eur = ds.adj_close_eur.loc[:ts, cols].tail(TRADING_DAYS + 10)
        bm_eur = ds.benchmark_eur().loc[:ts]
        block = _returns_block(adj_eur, bm_eur).reindex(tickers)
        for c in block.columns:
            snap[c] = block[c]

        # Marktkapitalisierung (Universum, Mio EUR) und Fundamentals.
        snap["market_cap"] = uni.frame["market_cap"].reindex(tickers)
        cur, prev = self._fundamentals(tickers, d)

        def f(col: str, frame: pd.DataFrame = cur) -> pd.Series:
            if col not in frame.columns:
                return pd.Series(np.nan, index=tickers, dtype=float)
            return pd.to_numeric(frame[col], errors="coerce").reindex(tickers) * scale

        for col in _FUND_CURRENT:
            snap[col] = f(col)
            snap[f"{col}_prev"] = f(col, prev)
        # shares_out in Mio Stück (Koyfin-Konvention), keine Währung.
        snap["shares_out"] = pd.to_numeric(cur.get("shares_out"), errors="coerce").reindex(tickers) / 1e6
        snap["shares_out_prev"] = pd.to_numeric(prev.get("shares_out"), errors="coerce").reindex(tickers) / 1e6

        mcap = snap["market_cap"]
        net_income = snap["net_income"]
        equity = f("equity")
        fcf = f("fcf")
        ebit = f("ebit")
        ebitda = f("ebitda")
        total_debt = snap["total_debt"]
        cash = f("cash")
        revenue = snap["revenue"]
        cogs = snap["cogs"]
        total_assets = snap["total_assets"]
        total_liab = f("total_liab")
        interest = f("interest_expense")
        retained = f("retained_earnings")
        ev = mcap + total_debt.fillna(0.0) - cash.fillna(0.0)
        ev = ev.where(total_debt.notna() | cash.notna(), mcap)

        snap["pe"] = _safe_ratio(mcap, net_income)
        snap["pb"] = _safe_ratio(mcap, equity)
        snap["pfcf"] = _safe_ratio(mcap, fcf)
        snap["fcf_yield"] = _safe_ratio(fcf, ev)
        snap["ev_ebitda"] = _safe_ratio(ev, ebitda)
        snap["ev_ebit"] = _safe_ratio(ev, ebit)
        snap["net_debt_ebitda"] = _safe_ratio(total_debt - cash, ebitda)
        snap["roe"] = _safe_ratio(net_income, equity)
        snap["roa"] = _safe_ratio(net_income, total_assets)
        invested = equity + total_debt - cash
        snap["roic"] = _safe_ratio(ebit * (1.0 - cfg.tax_rate_for(d)), invested)
        snap["gross_margin"] = _safe_ratio(revenue - cogs, revenue)
        snap["op_margin"] = _safe_ratio(ebit, revenue)
        snap["debt_equity"] = _safe_ratio(total_debt, equity)
        snap["int_coverage"] = _safe_ratio(ebit, interest).clip(upper=cfg.bt_int_coverage_cap)
        snap["current_ratio"] = _safe_ratio(snap["current_assets"], snap["current_liab"])
        # Altman Z (Original-Formel): 1,2·WC/TA + 1,4·RE/TA + 3,3·EBIT/TA
        # + 0,6·MktCap/TL + 1,0·Rev/TA.
        wc = snap["current_assets"] - snap["current_liab"]
        snap["altman_z"] = (
            1.2 * _safe_ratio(wc, total_assets)
            + 1.4 * _safe_ratio(retained, total_assets)
            + 3.3 * _safe_ratio(ebit, total_assets)
            + 0.6 * _safe_ratio(mcap, total_liab)
            + 1.0 * _safe_ratio(revenue, total_assets)
        )

        # Nicht verfügbar (Spec 4.3/4.4): EPS-Revisionen; Momentum-Proxy
        # optional (Sensitivität S8) als risikoadjustiertes 6-1-Momentum,
        # auf das Gültigkeitsband des Indikators geclippt.
        snap["eps_revisions_3m"] = np.nan
        if cfg.bt_momentum_proxy == "mom_6_1_adj":
            vol = snap["volatility_1y"]
            proxy = ((snap["ret_6m"] - snap["ret_1m"]) / vol).where(vol >= 0.05)
            lo, hi = V2_CLEAN_BOUNDS["eps_revisions_3m"]
            snap["eps_revisions_3m"] = proxy.clip(lo, hi)

        # Liquidität und IPO.
        vol_win = ds.volume.loc[:ts, cols].tail(63)
        close_win = ds.close.loc[:ts, cols].tail(63)
        adv = (vol_win * close_win).mean() * fx / 1e6
        snap["adv_3m"] = adv.reindex(tickers)
        snap["ipo_date"] = [
            (ds.first_price_date(t).date().isoformat() if ds.first_price_date(t) is not None else pd.NA)
            for t in tickers
        ]
        snap["export_date"] = d.isoformat()

        for col in SNAPSHOT_COLUMNS:
            if col not in snap.columns:
                snap[col] = np.nan
        snap = snap[list(SNAPSHOT_COLUMNS)].reset_index(drop=True)
        snap["ipo_date"] = snap["ipo_date"].astype("string")
        snap = assign_uids(snap)
        return snap

    # ── Benchmark-Proxy (Spec 4.5) ───────────────────────────────────────

    def benchmark_weights(self, snapshot: pd.DataFrame) -> pc.BenchmarkWeights:
        return benchmark_weights_proxy(snapshot, self.config)


def benchmark_weights_proxy(snapshot: pd.DataFrame, config: BacktestConfig) -> pc.BenchmarkWeights:
    """Kapitalisierungsgewichtete Sektoranteile der größten ``bt_benchmark_top_n``
    Titel des Universums (Proxy für den S&P 500); Region = 100 % USA."""
    top = snapshot.copy()
    top["market_cap"] = pd.to_numeric(top["market_cap"], errors="coerce")
    top = top.dropna(subset=["market_cap"]).sort_values("market_cap", ascending=False)
    top = top.head(int(config.bt_benchmark_top_n))
    if top.empty or float(top["market_cap"].sum()) <= 0:
        return pc.BenchmarkWeights(sector=None, region={REGION_US: 1.0}, diagnostics=[])
    sector = (top.groupby(top["sector"].fillna(SECTOR_UNKNOWN))["market_cap"].sum()
              / float(top["market_cap"].sum())).to_dict()
    return pc.BenchmarkWeights(sector=sector, region={REGION_US: 1.0}, diagnostics=[])


# ── CSV-Export (Debug-Meilenstein: produktiver Import liest ihn ein) ──────


def write_snapshot_csv(snapshot: pd.DataFrame, path: str | Path | None = None) -> str:
    """Snapshot als Koyfin-kompatible CSV (``;``-getrennt, Dezimalkomma).

    Optionale Spalten stehen hinter den 57 Basisspalten; ``volatility_1y``
    wird — wie im Koyfin-Export — in Prozent geschrieben, weil der Loader
    durch 100 teilt. Liefert den CSV-Text (und schreibt ihn, wenn ``path``).
    """
    df = snapshot[list(SNAPSHOT_COLUMNS)].copy()
    df["volatility_1y"] = pd.to_numeric(df["volatility_1y"], errors="coerce") * 100.0
    buf = io.StringIO()
    df.to_csv(buf, sep=";", decimal=",", index=False)
    text = buf.getvalue()
    if path is not None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(text, encoding="utf-8")
    return text
