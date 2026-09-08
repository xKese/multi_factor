"""Tests 9–12: Delisting, Buchhaltung, Kalender, produktiver Code."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.simulator import (
    Ledger,
    Simulator,
    rebalance_schedule,
    run_backtest,
)
from app.core import portfolio_construction as pc
from app.core import scoring_v2
from app.core.config import Settings

from .conftest import make_dataset, small_config


def _fixed_target(uids: list[str]):
    """Stub für ``Simulator.build_target``: Gleichgewichtung fester Titel am
    ersten Stichtag, danach keine Änderung."""
    state = {"done": False}

    def _build(self, t, mode, current, scored):
        if state["done"]:
            target = dict(current)
        else:
            target = {u: 1.0 / len(uids) for u in uids}
            state["done"] = True
        return {"target": target, "trades": pd.DataFrame(), "portfolio": pd.DataFrame(),
                "diagnostics": [], "te": None, "te_coverage": None, "turnover": 0.0,
                "n_deferred": 0, "n_candidates": 0}

    return _build


def test_delisting(monkeypatch):
    """Verkauf am letzten Kurstag, Cash, Kosten; Haircut-Variante (Test 9)."""
    ds = make_dataset(n=10)
    end_day = pd.Timestamp("2014-05-15")
    ds.adj_close.loc[ds.calendar > end_day, "T05"] = np.nan
    ds.close.loc[ds.calendar > end_day, "T05"] = np.nan
    ds.delisted = pd.DataFrame([{"symbol": "T05", "delisting_date": pd.Timestamp("2014-05-16")}])
    cfg = small_config(bt_start=date(2014, 3, 31), bt_end=date(2014, 12, 31))
    monkeypatch.setattr(Simulator, "build_target", _fixed_target(["T01", "T05", "T07"]))

    res = run_backtest(cfg, ds)
    dl = res.trades[res.trades["action"] == "DELISTING"]
    assert len(dl) == 1
    row = dl.iloc[0]
    last_eur = ds.adj_close_eur.loc[end_day, "T05"]
    assert row["uid"] == "T05"
    # Verkauf zum letzten Adjusted Close, am ersten Handelstag ohne Kurs.
    assert row["price_eur"] == pytest.approx(last_eur)
    assert pd.Timestamp(row["date"]) == ds.calendar[ds.calendar > end_day][0]
    assert row["cost_eur"] == pytest.approx(abs(row["notional_eur"]) * cfg.cost_rate())
    nav = res.nav_daily
    day_before = nav.loc[end_day]
    day_after = nav.loc[pd.Timestamp(row["date"])]
    assert day_before["n_positions"] == 3 and day_after["n_positions"] == 2
    assert day_after["cash"] > day_before["cash"] + abs(row["notional_eur"]) - row["cost_eur"] - 1.0
    assert res.rebalances["n_delistings"].sum() == 1
    # Diagnose zählt fehlende Kurse nicht für den delisteten Titel (verkauft).
    assert nav.loc[nav.index > pd.Timestamp(row["date"]), "missing_prices"].sum() == 0

    # Haircut-Variante (S7): Erlös −30 %.
    cfg7 = small_config(bt_start=date(2014, 3, 31), bt_end=date(2014, 12, 31),
                        bt_delisting_haircut=0.30)
    monkeypatch.setattr(Simulator, "build_target", _fixed_target(["T01", "T05", "T07"]))
    res7 = run_backtest(cfg7, ds)
    row7 = res7.trades[res7.trades["action"] == "DELISTING"].iloc[0]
    assert row7["price_eur"] == pytest.approx(last_eur * 0.7)
    assert abs(row7["notional_eur"]) == pytest.approx(abs(row["notional_eur"]) * 0.7)
    assert res7.nav_daily["nav"].iloc[-1] < res.nav_daily["nav"].iloc[-1]


def test_simulator_accounting(dataset, config):
    """NAV = Cash + Positionen; Kosten korrekt; ganze Aktien; kein negatives
    Cash (Test 10)."""
    res = run_backtest(config, dataset)
    nav = res.nav_daily
    assert (nav["cash"] >= -1e-6).all()
    assert (nav["nav"] > 0).all()
    assert nav.loc[nav["rebalance"], "n_positions"].min() >= config.settings().pc_min_n

    # Bestände je Stichtag: Σ Wert + Cash = NAV, Stückzahlen ganzzahlig.
    holdings = res.holdings
    for d, grp in holdings.groupby("date"):
        row = nav.loc[pd.Timestamp(d)]
        assert grp["value_eur"].sum() + row["cash"] == pytest.approx(row["nav"], rel=1e-9)
        assert (grp["shares"] == grp["shares"].round()).all()
        assert grp["weight"].sum() == pytest.approx(1.0 - row["cash"] / row["nav"], abs=1e-9)
    trades = res.trades
    assert (trades["shares"] == trades["shares"].round()).all()
    assert (trades["cost_eur"] >= 0).all()
    np.testing.assert_allclose(trades["cost_eur"], trades["notional_eur"].abs() * config.cost_rate())
    assert trades["cost_eur"].sum() == pytest.approx(res.meta["total_costs_eur"])
    # Käufe wurden nie über das verfügbare Cash hinaus ausgeführt (Rest bleibt Cash).
    assert (nav.loc[nav["rebalance"], "cash"] / nav.loc[nav["rebalance"], "nav"]).max() < 0.01

    # Ledger direkt: Rundung auf ganze Aktien, Kosten je Seite, Cash nie negativ.
    led = Ledger(cash=1000.0, cost_rate=0.0015)
    trades, failed = led.rebalance_to({"A": 0.5, "B": 0.5}, {"A": 30.0, "B": 7.0}, date(2024, 1, 2))
    assert failed == 0
    assert led.positions == {"A": 16.0, "B": 71.0}
    assert led.cash >= 0
    assert led.nav() == pytest.approx(led.cash + 16 * 30 + 71 * 7)
    assert led.total_costs == pytest.approx((16 * 30 + 71 * 7) * 0.0015)
    # Verkauf komplett + Kauf ohne Kurs → nicht ausführbar, Gewicht bleibt Cash.
    trades, failed = led.rebalance_to({"C": 1.0}, {"A": 30.0, "B": 7.0}, date(2024, 1, 3))
    assert failed == 1 and led.positions == {} and led.cash > 990
    # Kosten × 2 (S6) senken das Endvermögen.
    cfg6 = small_config(bt_commission_bps=20.0, bt_slippage_bps=10.0)
    res6 = run_backtest(cfg6, dataset)
    assert res6.meta["total_costs_eur"] == pytest.approx(res.meta["total_costs_eur"] * 2, rel=0.05)


def test_calendar():
    """März/September full, Juni/Dezember interim; erster Stichtag full (Test 11)."""
    cal = pd.bdate_range("2013-01-01", "2015-12-31")
    s = Settings()
    sched = rebalance_schedule(cal, date(2013, 1, 1), date(2015, 12, 31), s)
    assert len(sched) == 12
    for d, mode in sched:
        assert d.month in (3, 6, 9, 12)
        assert mode == ("full" if d.month in (3, 9) else "interim")
        assert d == cal[(cal.year == d.year) & (cal.month == d.month)][-1].date()
    assert sched[0] == (date(2013, 3, 29), "full")
    # Start im Juni: erster Stichtag (30.06.) ist trotzdem full.
    sched2 = rebalance_schedule(cal, date(2013, 6, 1), date(2013, 12, 31), s)
    assert sched2[0] == (date(2013, 6, 28), "full")
    assert sched2[1] == (date(2013, 9, 30), "full") and sched2[2] == (date(2013, 12, 31), "interim")
    # S5: jedes Quartal full.
    s5 = Settings()
    s5.pc_rebalance_months = [3, 6, 9, 12]
    s5.pc_interim_months = []
    assert all(m == "full" for _, m in rebalance_schedule(cal, date(2013, 1, 1), date(2013, 12, 31), s5))


def test_production_code_reused(monkeypatch):
    """Monkeypatch auf select_portfolio etc. wird vom Simulator getroffen —
    es existiert keine Kopie des Modellcodes (Test 12)."""
    ds = make_dataset(n=24)
    cfg = small_config(bt_start=date(2014, 3, 31), bt_end=date(2014, 7, 31))
    calls: dict[str, int] = {}

    def _spy(module, name):
        original = getattr(module, name)

        def wrapper(*args, **kwargs):
            calls[name] = calls.get(name, 0) + 1
            return original(*args, **kwargs)

        monkeypatch.setattr(module, name, wrapper)

    for name in ("select_portfolio", "compute_weights", "apply_te_constraint",
                 "build_trade_list", "build_model_portfolio"):
        _spy(pc, name)
    _spy(scoring_v2, "compute_scores_v2")
    _spy(scoring_v2, "derive_v2_indicators")
    from app.core import scoring, universe_filter
    _spy(scoring, "compute_scores")
    _spy(universe_filter, "apply_universe_filters")

    res = run_backtest(cfg, ds)
    assert list(res.rebalances["mode"]) == ["full", "interim"]
    for name in ("select_portfolio", "compute_weights", "apply_te_constraint", "build_trade_list",
                 "compute_scores_v2", "derive_v2_indicators", "compute_scores",
                 "apply_universe_filters"):
        assert calls.get(name, 0) >= 1, name
    # Interim-Stichtag läuft über den produktiven Orchestrator.
    assert calls.get("build_model_portfolio", 0) == 1

    # Ein Monkeypatch mit anderem Ergebnis verändert das Portfolio: leere
    # Selektion → keine Positionen.
    monkeypatch.setattr(
        pc, "select_portfolio",
        lambda uni, cur, bm, s, overrides=None, snapshot_date=None: pc.SelectionResult(
            portfolio=uni.iloc[0:0], exits=pd.DataFrame(columns=["uid", "reason"]), skipped=[],
            diagnostics=[]),
    )
    res2 = run_backtest(cfg, ds)
    assert res2.nav_daily["n_positions"].max() == 0
