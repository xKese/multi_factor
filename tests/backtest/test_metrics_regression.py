"""Tests 13–14: Kennzahlen gegen Handrechnung, Faktorregression."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.backtest import metrics as mt
from app.backtest.factor_regression import (
    FACTORS,
    flag_loading,
    merge_factors,
    parse_french_csv,
    regress,
)


def test_metrics():
    """Kennzahlen gegen handgerechnete Referenzwerte einer 10-Tage-Reihe (Test 13)."""
    r_p = [0.01, -0.02, 0.03, 0.0, 0.01, -0.01, 0.02, 0.005, -0.015]
    r_b = [0.002] * 9
    idx = pd.bdate_range("2024-01-01", periods=10)
    nav = pd.Series(100.0 * np.cumprod([1.0, *[1 + r for r in r_p]]), index=idx)
    bm = pd.Series(100.0 * np.cumprod([1.0, *[1 + r for r in r_b]]), index=idx)

    m = mt.summary(nav, bm)
    pf, b, ac = m["portfolio"], m["benchmark"], m["active"]

    total = float(np.prod([1 + r for r in r_p]) - 1)
    assert pf["total_return"] == pytest.approx(total)
    assert pf["ann_return"] == pytest.approx((1 + total) ** (252 / 9) - 1)
    logs = [math.log(1 + r) for r in r_p]
    vol = float(np.std(logs, ddof=1) * math.sqrt(252))
    assert pf["volatility"] == pytest.approx(vol)
    assert pf["sharpe"] == pytest.approx(pf["ann_return"] / vol)
    # Max. Drawdown: Hoch 101 (Tag 2) → Tief 98,98 (Tag 3): −2,0 %; Erholung Tag 4 (101,95).
    assert pf["max_drawdown"] == pytest.approx(98.98 / 101.0 - 1)
    assert pf["max_drawdown_date"] == idx[2].date()
    assert pf["recovery_days"] == 1
    assert pf["calmar"] == pytest.approx(pf["ann_return"] / abs(98.98 / 101.0 - 1))
    active = np.array(r_p) - np.array(r_b)
    te = float(np.std(active, ddof=1) * math.sqrt(252))
    assert ac["tracking_error"] == pytest.approx(te)
    b_total = 1.002**9 - 1
    assert b["total_return"] == pytest.approx(b_total)
    assert ac["ann_return"] == pytest.approx(pf["ann_return"] - ((1 + b_total) ** (252 / 9) - 1))
    assert ac["information_ratio"] == pytest.approx(ac["ann_return"] / te)
    assert math.isnan(b["max_drawdown"]) or b["max_drawdown"] == pytest.approx(0.0)
    assert b["recovery_days"] is None or b["recovery_days"] == 0
    # Beta gegen eine konstante Benchmark ist nicht definiert (Varianz 0).
    assert math.isnan(ac["beta"])

    # Kalenderjahr-Trefferquote: 2 Jahre, aktiv > 0 in genau einem.
    idx2 = pd.bdate_range("2022-12-30", periods=400)
    nav2 = pd.Series(100.0, index=idx2)
    nav2[idx2.year == 2023] = np.linspace(100, 110, int((idx2.year == 2023).sum()))
    nav2[idx2.year == 2024] = np.linspace(110, 105, int((idx2.year == 2024).sum()))
    bm2 = pd.Series(np.linspace(100, 108, len(idx2)), index=idx2)
    cy = mt.calendar_year_returns(nav2)
    assert list(cy.index) == [2022, 2023, 2024]
    assert cy.loc[2023] == pytest.approx(0.10)
    assert cy.loc[2024] == pytest.approx(105 / 110 - 1)
    assert mt.hit_rate(nav2, bm2) == pytest.approx(1 / 3)
    table = mt.calendar_year_table(nav2, bm2, None, None)
    assert set(table.columns) == {"portfolio", "benchmark", "active", "turnover", "costs_bp"}

    rebal = pd.DataFrame({"date": ["2023-03-31", "2023-09-29"], "turnover_oneway": [0.2, 0.1]})
    assert mt.turnover_pa(rebal, 252) == pytest.approx(0.3)
    assert mt.costs_bp_pa(1000.0, pd.Series([1e6] * 253, index=pd.bdate_range("2023-01-01", periods=253))) == pytest.approx(10.0)


def test_factor_regression():
    """Regression auf synthetischen Faktoren reproduziert bekannte
    Koeffizienten (Test 14)."""
    rng = np.random.default_rng(3)
    n = 1500
    idx = pd.bdate_range("2015-01-01", periods=n)
    factors = pd.DataFrame(
        {f: rng.normal(0.0, 0.008, n) for f in FACTORS}, index=idx
    )
    factors["RF"] = 0.0001
    true = {"Mkt-RF": 1.1, "SMB": -0.2, "HML": 0.3, "RMW": 0.25, "CMA": 0.15, "Mom": 0.4}
    alpha = 0.0002
    y = alpha + sum(true[f] * factors[f] for f in FACTORS) + rng.normal(0, 0.002, n)
    res = regress(pd.Series(y, index=idx), factors)
    for f in FACTORS:
        assert res["betas"][f] == pytest.approx(true[f], abs=0.03), f
        assert abs(res["tstats"][f]) > 5
    assert res["alpha_daily"] == pytest.approx(alpha, abs=0.0002)
    assert res["alpha_pa"] == pytest.approx(res["alpha_daily"] * 252)
    assert res["r2"] > 0.9 and res["n"] == n
    assert flag_loading("HML", 0.3, 4.0) is False
    assert flag_loading("HML", -0.3, 4.0) is True
    assert flag_loading("Mom", 0.3, 1.0) is True
    assert flag_loading("SMB", 0.02, 0.5) is False

    # Kenneth-French-Format (Kopfzeilen, Prozentwerte, Blockende an Leerzeile).
    ff5 = (
        "This file was created by CMPT_ME_BEME_OP_INV_RETS_DAILY using the 202312 CRSP database.\n"
        "\n"
        ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
        "20240102,1.10,-0.20,0.30,0.10,0.05,0.020\n"
        "20240103,-0.50,0.10,0.00,0.20,-0.10,0.020\n"
        "\n"
        "Annual Factors: January-December\n"
        "2023,20.1,-3.2,1.0,2.0,0.5,4.9\n"
    )
    mom = "\n,Mom   \n20240102,0.80\n20240103,-0.40\n\n"
    df5 = parse_french_csv(ff5.encode("utf-8"))
    assert list(df5.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF"]
    assert df5.loc[pd.Timestamp("2024-01-02"), "Mkt-RF"] == pytest.approx(0.011)
    assert len(df5) == 2
    dfm = parse_french_csv(mom)
    assert list(dfm.columns) == ["Mom"] and dfm.iloc[1, 0] == pytest.approx(-0.004)
    merged = merge_factors(df5, dfm)
    assert list(merged.columns) == ["Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF", "Mom"]
