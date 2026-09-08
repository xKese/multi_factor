"""Kennzahlen des Backtests (Spec 7). Alle Werte in EUR auf Basis
täglicher NAV-Reihen; annualisiert mit 252 Handelstagen."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _ann_return(nav: pd.Series) -> float:
    nav = nav.dropna()
    if len(nav) < 2 or nav.iloc[0] <= 0:
        return float("nan")
    total = nav.iloc[-1] / nav.iloc[0]
    years = (len(nav) - 1) / TRADING_DAYS
    if years <= 0 or total <= 0:
        return float("nan")
    return float(total ** (1.0 / years) - 1.0)


def _vol(nav: pd.Series) -> float:
    r = np.log(nav / nav.shift(1)).dropna()
    if len(r) < 2:
        return float("nan")
    return float(r.std(ddof=1) * math.sqrt(TRADING_DAYS))


def drawdown_series(nav: pd.Series) -> pd.Series:
    return nav / nav.cummax() - 1.0


def max_drawdown(nav: pd.Series) -> dict:
    """Max. Drawdown, Datum des Tiefs und Erholungsdauer in Handelstagen
    (None, wenn bis zum Ende nicht erholt)."""
    nav = nav.dropna()
    if nav.empty:
        return {"max_drawdown": float("nan"), "max_drawdown_date": None, "recovery_days": None}
    dd = drawdown_series(nav)
    trough = dd.idxmin()
    peak_level = nav.loc[:trough].max()
    after = nav.loc[trough:]
    recovered = after[after >= peak_level]
    recovery = None
    if not recovered.empty and recovered.index[0] != trough:
        recovery = int(nav.index.get_loc(recovered.index[0]) - nav.index.get_loc(trough))
    return {
        "max_drawdown": float(dd.min()),
        "max_drawdown_date": trough.date() if hasattr(trough, "date") else trough,
        "recovery_days": recovery,
    }


def _simple_returns(nav: pd.Series) -> pd.Series:
    return nav.pct_change(fill_method=None).dropna()


def tracking_error(nav: pd.Series, bm: pd.Series) -> float:
    both = pd.concat([_simple_returns(nav), _simple_returns(bm)], axis=1).dropna()
    if len(both) < 2:
        return float("nan")
    active = both.iloc[:, 0] - both.iloc[:, 1]
    return float(active.std(ddof=1) * math.sqrt(TRADING_DAYS))


def beta(nav: pd.Series, bm: pd.Series) -> float:
    both = pd.concat([_simple_returns(nav), _simple_returns(bm)], axis=1).dropna()
    if len(both) < 3 or float(both.iloc[:, 1].var(ddof=1)) <= 1e-16:
        return float("nan")
    return float(both.iloc[:, 0].cov(both.iloc[:, 1]) / both.iloc[:, 1].var(ddof=1))


def calendar_year_returns(nav: pd.Series) -> pd.Series:
    """Rendite je Kalenderjahr (erstes Jahr ab Start, letztes bis Ende)."""
    nav = nav.dropna()
    if nav.empty:
        return pd.Series(dtype=float)
    year_end = nav.groupby(nav.index.year).last()
    prev = year_end.shift(1)
    prev.iloc[0] = nav.iloc[0]
    return year_end / prev - 1.0


def hit_rate(nav: pd.Series, bm: pd.Series) -> float:
    pf = calendar_year_returns(nav)
    b = calendar_year_returns(bm)
    both = pd.concat([pf, b], axis=1).dropna()
    if both.empty:
        return float("nan")
    return float(((both.iloc[:, 0] - both.iloc[:, 1]) > 0).mean())


def rolling_12m_active(nav: pd.Series, bm: pd.Series) -> pd.Series:
    """Rollierende 12-Monats-Differenz der Renditen (Portfolio − Benchmark)."""
    both = pd.concat([nav, bm], axis=1).dropna()
    if len(both) <= TRADING_DAYS:
        return pd.Series(dtype=float)
    pf = both.iloc[:, 0] / both.iloc[:, 0].shift(TRADING_DAYS) - 1.0
    b = both.iloc[:, 1] / both.iloc[:, 1].shift(TRADING_DAYS) - 1.0
    return (pf - b).dropna()


def summary(nav: pd.Series, bm: pd.Series, rf: float = 0.0) -> dict[str, dict]:
    """Kennzahlentabelle Portfolio / Benchmark / aktiv."""
    nav = nav.dropna()
    bm = bm.reindex(nav.index).ffill().dropna()
    nav = nav.reindex(bm.index)

    def _block(series: pd.Series) -> dict:
        ann = _ann_return(series)
        vol = _vol(series)
        dd = max_drawdown(series)
        return {
            "total_return": float(series.iloc[-1] / series.iloc[0] - 1.0) if len(series) > 1 else float("nan"),
            "ann_return": ann,
            "volatility": vol,
            "sharpe": (ann - rf) / vol if vol and not math.isnan(vol) and vol > 0 else float("nan"),
            "max_drawdown": dd["max_drawdown"],
            "max_drawdown_date": dd["max_drawdown_date"],
            "recovery_days": dd["recovery_days"],
            "calmar": ann / abs(dd["max_drawdown"]) if dd["max_drawdown"] and dd["max_drawdown"] < 0 else float("nan"),
        }

    pf = _block(nav)
    b = _block(bm)
    te = tracking_error(nav, bm)
    active_ann = pf["ann_return"] - b["ann_return"]
    roll = rolling_12m_active(nav, bm)
    active = {
        "total_return": pf["total_return"] - b["total_return"],
        "ann_return": active_ann,
        "tracking_error": te,
        "information_ratio": active_ann / te if te and not math.isnan(te) and te > 0 else float("nan"),
        "beta": beta(nav, bm),
        "hit_rate": hit_rate(nav, bm),
        "best_12m": float(roll.max()) if len(roll) else float("nan"),
        "best_12m_date": roll.idxmax().date() if len(roll) else None,
        "worst_12m": float(roll.min()) if len(roll) else float("nan"),
        "worst_12m_date": roll.idxmin().date() if len(roll) else None,
        "max_rel_drawdown": max_drawdown(nav / bm)["max_drawdown"],
    }
    return {"portfolio": pf, "benchmark": b, "active": active}


def turnover_pa(rebalances: pd.DataFrame, n_trading_days: int) -> float:
    if rebalances is None or rebalances.empty or "turnover_oneway" not in rebalances:
        return float("nan")
    years = max(n_trading_days / TRADING_DAYS, 1e-9)
    return float(pd.to_numeric(rebalances["turnover_oneway"], errors="coerce").fillna(0.0).sum() / years)


def costs_bp_pa(total_costs: float, nav: pd.Series) -> float:
    nav = nav.dropna()
    if nav.empty or float(nav.mean()) <= 0:
        return float("nan")
    years = max((len(nav) - 1) / TRADING_DAYS, 1e-9)
    return float(total_costs / float(nav.mean()) / years * 1e4)


def calendar_year_table(
    nav: pd.Series, bm: pd.Series, rebalances: pd.DataFrame | None, trades: pd.DataFrame | None
) -> pd.DataFrame:
    """Kalenderjahrestabelle: Portfolio, Benchmark, aktiv, Turnover, Kosten (bp)."""
    pf = calendar_year_returns(nav)
    b = calendar_year_returns(bm.reindex(nav.index).ffill())
    out = pd.DataFrame({"portfolio": pf, "benchmark": b})
    out["active"] = out["portfolio"] - out["benchmark"]
    turnover = pd.Series(dtype=float)
    if rebalances is not None and not rebalances.empty and "turnover_oneway" in rebalances:
        r = rebalances.copy()
        r["year"] = pd.to_datetime(r["date"]).dt.year
        turnover = pd.to_numeric(r["turnover_oneway"], errors="coerce").groupby(r["year"]).sum()
    costs = pd.Series(dtype=float)
    if trades is not None and not trades.empty and "cost_eur" in trades:
        tr = trades.copy()
        tr["year"] = pd.to_datetime(tr["date"]).dt.year
        cost_by_year = pd.to_numeric(tr["cost_eur"], errors="coerce").groupby(tr["year"]).sum()
        avg_nav = nav.groupby(nav.index.year).mean()
        costs = (cost_by_year / avg_nav.reindex(cost_by_year.index) * 1e4)
    out["turnover"] = turnover.reindex(out.index)
    out["costs_bp"] = costs.reindex(out.index)
    out.index.name = "year"
    return out


def rolling_3y(nav: pd.Series, bm: pd.Series, window: int = 3 * TRADING_DAYS) -> pd.DataFrame:
    """Rollierende 3-Jahres-Reihen: aktive Rendite p. a., TE, IR, Beta."""
    both = pd.concat([nav, bm], axis=1).dropna()
    if len(both) <= window:
        return pd.DataFrame(columns=["active_return", "te", "ir", "beta"])
    r = both.pct_change(fill_method=None).dropna()
    pf_r, bm_r = r.iloc[:, 0], r.iloc[:, 1]
    active = pf_r - bm_r
    pf_ann = (both.iloc[:, 0] / both.iloc[:, 0].shift(window)) ** (TRADING_DAYS / window) - 1.0
    bm_ann = (both.iloc[:, 1] / both.iloc[:, 1].shift(window)) ** (TRADING_DAYS / window) - 1.0
    te = active.rolling(window).std(ddof=1) * math.sqrt(TRADING_DAYS)
    cov = pf_r.rolling(window).cov(bm_r)
    var = bm_r.rolling(window).var(ddof=1)
    out = pd.DataFrame(
        {
            "active_return": (pf_ann - bm_ann),
            "te": te,
            "beta": cov / var,
        }
    )
    out["ir"] = out["active_return"] / out["te"]
    return out.dropna(subset=["active_return"])


def diagnostics_summary(rebalances: pd.DataFrame, holdings: pd.DataFrame) -> dict:
    """Ø Titel, Ø Haltedauer (Stichtage), Anteile Notfüllung / verschobene
    Trades / TE nicht erfüllbar, fehlende Kurse, Delistings, Unknown-Sektor."""
    if rebalances is None or rebalances.empty:
        return {}
    n = len(rebalances)
    n_deferred = pd.to_numeric(rebalances.get("n_deferred"), errors="coerce").fillna(0)
    n_trades = pd.to_numeric(rebalances.get("n_trades"), errors="coerce").fillna(0)
    total_trades = float((n_trades + n_deferred).sum())
    holding_periods = float("nan")
    if holdings is not None and not holdings.empty:
        spans = holdings.groupby("uid")["date"].nunique()
        holding_periods = float(spans.mean())
    return {
        "avg_positions": float(pd.to_numeric(rebalances["n_positions"], errors="coerce").mean()),
        "avg_holding_rebalances": holding_periods,
        "fill_zone_share": float(rebalances["fill_zone_used"].astype(bool).mean()),
        "deferred_share": float(n_deferred.sum() / total_trades) if total_trades > 0 else 0.0,
        "te_unmet_share": float(rebalances["te_unmet"].astype(bool).mean()),
        "te_skipped_share": float(rebalances["te_skipped"].astype(bool).mean()),
        "below_min_share": float(rebalances["below_min"].astype(bool).mean()),
        "missing_prices_total": int(pd.to_numeric(rebalances["n_missing_prices"], errors="coerce").sum()),
        "delistings_total": int(pd.to_numeric(rebalances["n_delistings"], errors="coerce").sum()),
        "buy_failed_total": int(pd.to_numeric(rebalances["n_buy_failed"], errors="coerce").sum()),
        "unknown_sector_share": float(pd.to_numeric(rebalances["unknown_sector_share"], errors="coerce").mean()),
        "avg_cash_share": float(pd.to_numeric(rebalances["cash_share"], errors="coerce").mean()),
        "n_rebalances": int(n),
        "universe_min": int(rebalances["n_universe"].min()),
        "universe_median": float(rebalances["n_universe"].median()),
        "universe_max": int(rebalances["n_universe"].max()),
    }
