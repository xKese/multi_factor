"""Tests für Rebalancing-Kalender und Turnover-Budget (Spec 13,
Tests 18–20)."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.core.config import Settings
from app.core.portfolio_construction import (
    ACTION_BUY,
    ACTION_DEFERRED,
    ACTION_HOLD,
    ACTION_SELL,
    MODE_FULL,
    MODE_INTERIM,
    MODE_MONITOR,
    build_model_portfolio,
    build_trade_list,
    detect_rebalance_mode,
)


def test_rebalance_mode_detection():
    """Modi aus Snapshot-Datum und letzter Portfolioversion (Test 18)."""
    s = Settings()

    # Ohne Vorversion: erster Lauf ist ein Voll-Rebalancing.
    assert detect_rebalance_mode(date(2026, 4, 5), s, None) == MODE_FULL

    # Erster Import nach dem letzten Handelstag im März (31.03.2026, Di).
    meta_jan = {"snapshot_date": date(2026, 1, 10)}
    assert detect_rebalance_mode(date(2026, 4, 5), s, meta_jan) == MODE_FULL

    # Bereits nach dem März-Trigger gebaut → kein Full, kein Interim fällig.
    meta_apr = {"snapshot_date": date(2026, 4, 2)}
    assert detect_rebalance_mode(date(2026, 4, 5), s, meta_apr) == MODE_MONITOR

    # Erster Import nach Ende Juni (30.06.2026) → Interim.
    assert detect_rebalance_mode(date(2026, 7, 3), s, meta_apr) == MODE_INTERIM

    # Import AM letzten Handelstag zählt noch nicht als "nach" dem Trigger.
    meta_jan5 = {"snapshot_date": date(2026, 1, 5)}
    assert (
        detect_rebalance_mode(date(2026, 3, 31), s, meta_jan5) == MODE_MONITOR
    )
    assert detect_rebalance_mode(date(2026, 4, 1), s, meta_jan5) == MODE_FULL

    # Oktober-Build → Import Ende März: der Dezember-Interim-Trigger ist
    # inzwischen verstrichen → Interim.
    meta_oct = {"snapshot_date": date(2025, 10, 2)}
    assert detect_rebalance_mode(date(2026, 3, 31), s, meta_oct) == MODE_INTERIM


def _target(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    df.index = pd.Index(df["uid"], name="_uid")
    return df


def test_turnover_priority():
    """Streichung in Prioritätsreihenfolge; Filter-Verkäufe werden nie
    gestrichen; gestrichene Trades bleiben als VERSCHOBEN sichtbar
    (Test 19)."""
    s = Settings()
    s.pc_turnover_budget_full = 0.20
    current = {"F1": 0.10, "V1": 0.10, "V2": 0.10, "K": 0.30, "R": 0.40}
    target = _target(
        [
            {"uid": "K", "weight_effective": 0.40, "composite_z": 0.5,
             "zone_v2": "HALTEN", "reason": "zone_HALTEN"},
            {"uid": "R", "weight_effective": 0.20, "composite_z": 0.2,
             "zone_v2": "HALTEN", "reason": "zone_HALTEN"},
            {"uid": "B1", "weight_effective": 0.20, "composite_z": 2.0,
             "zone_v2": "KANDIDAT", "reason": "zone_KANDIDAT"},
            {"uid": "B2", "weight_effective": 0.20, "composite_z": 1.0,
             "zone_v2": "KANDIDAT", "reason": "zone_KANDIDAT"},
        ]
    )
    universe = _target(
        [
            {"uid": "F1", "composite_z": -0.5, "zone_v2": "FILTER"},
            {"uid": "V1", "composite_z": -2.0, "zone_v2": "VERKAUFEN"},
            {"uid": "V2", "composite_z": -1.0, "zone_v2": "VERKAUFEN"},
        ]
    )
    exit_reasons = {
        "F1": "FILTER: market_cap",
        "V1": "zone_VERKAUFEN",
        "V2": "zone_VERKAUFEN",
    }
    result = build_trade_list(
        target, current, s, MODE_FULL, universe=universe, exit_reasons=exit_reasons
    )
    trades = result.trades.set_index("uid")

    # Verkäufe (Pflicht + Budget): F1 (FILTER) nie gestrichen, V1/V2 passen
    # ins Budget (je 0,05 einseitig).
    assert trades.loc["F1", "action"] == ACTION_SELL
    assert trades.loc["V1", "action"] == ACTION_SELL
    assert trades.loc["V2", "action"] == ACTION_SELL
    # Käufe (je 0,10) sprengen das Budget → verschoben, sichtbar.
    assert trades.loc["B1", "action"] == ACTION_DEFERRED
    assert trades.loc["B2", "action"] == ACTION_DEFERRED
    assert "turnover_budget" in trades.loc["B1", "reason"]
    assert result.n_deferred >= 2
    assert any(d.code == "turnover_budget_exceeded" for d in result.diagnostics)
    # Budget eingehalten.
    assert result.turnover_oneway <= s.pc_turnover_budget_full + 1e-9

    # Ohne Budgetdruck: kein VERSCHOBEN, Kleinst-Deltas als HALTEN.
    s2 = Settings()
    s2.pc_turnover_budget_full = 1.0
    result2 = build_trade_list(
        target, current, s2, MODE_FULL, universe=universe, exit_reasons=exit_reasons
    )
    t2 = result2.trades.set_index("uid")
    assert (t2["action"] != ACTION_DEFERRED).all()
    assert t2.loc["B1", "action"] == ACTION_BUY
    assert t2.loc["K", "action"] != ACTION_HOLD  # Δw = 0,10 ist ein Trade


def test_interim_mode_no_reweight():
    """Interim: nur Verkäufe/Ersatz; bestehende Gewichte bleiben bis auf
    Renormierung unverändert (Test 20)."""
    s = Settings()
    # Budget großzügig, damit hier nur die Interim-Mechanik geprüft wird
    # (die Budget-Streichung testet test_turnover_priority).
    s.pc_turnover_budget_interim = 1.0
    universe = pd.DataFrame(
        [
            {"uid": "A", "sector": "X", "region": "Europe", "zone_v2": "HALTEN",
             "composite_z": 0.5, "composite_pct": 0.7, "volatility_1y": 0.2},
            {"uid": "B", "sector": "X", "region": "Europe", "zone_v2": "KANDIDAT",
             "composite_z": 1.5, "composite_pct": 0.9, "volatility_1y": 0.2},
            {"uid": "C", "sector": "X", "region": "Europe", "zone_v2": "VERKAUFEN",
             "composite_z": -1.0, "composite_pct": 0.3, "volatility_1y": 0.2},
            {"uid": "D", "sector": "X", "region": "Europe", "zone_v2": "KANDIDAT",
             "composite_z": 2.0, "composite_pct": 0.95, "volatility_1y": 0.2},
        ]
    )
    current = {"A": 0.3, "B": 0.3, "C": 0.4}
    result = build_model_portfolio(
        universe, s, current, mode=MODE_INTERIM, snapshot_date=date(2026, 7, 1)
    )
    portfolio = result["portfolio"].set_index("uid")

    # C verkauft, D als Ersatz mit dem freigewordenen Gewicht.
    assert set(portfolio.index) == {"A", "B", "D"}
    assert portfolio.loc["A", "weight_effective"] == pytest.approx(0.3)
    assert portfolio.loc["B", "weight_effective"] == pytest.approx(0.3)
    assert portfolio.loc["D", "weight_effective"] == pytest.approx(0.4)

    trades = result["trades"].trades.set_index("uid")
    assert trades.loc["C", "action"] == ACTION_SELL
    assert trades.loc["D", "action"] == ACTION_BUY
    assert trades.loc["A", "action"] == ACTION_HOLD
    assert trades.loc["B", "action"] == ACTION_HOLD
    assert result["meta"]["rebalance_mode"] == MODE_INTERIM


def test_monitor_mode_no_update():
    """Monitor: kein Zielportfolio-Update; Filter-Fails als
    Sofortmaßnahme-Vorschlag (Spec 7.1)."""
    s = Settings()
    universe = pd.DataFrame(
        [
            {"uid": "A", "zone_v2": "FILTER", "composite_z": -1.0,
             "composite_pct": 0.2, "filter_reasons": ["market_cap"]},
            {"uid": "B", "zone_v2": "HALTEN", "composite_z": 0.5,
             "composite_pct": 0.7, "filter_reasons": []},
        ]
    )
    result = build_model_portfolio(
        universe, s, {"A": 0.5, "B": 0.5}, mode="monitor",
        snapshot_date=date(2026, 8, 1),
    )
    assert result["portfolio"].empty
    assert result["trades"].trades.empty
    assert any(
        d.code == "monitor_filter_fail" and d.uid == "A"
        for d in result["diagnostics"]
    )
