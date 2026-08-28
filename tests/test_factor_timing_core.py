"""Tests für die Factor-Timing-Kernlogik (Regime v2, Tilts, Auto-Signale)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core import factor_timing as ft


# ── Regime v2 ───────────────────────────────────────────────────────────────


def test_stagflation_reachable_with_falling_pmi():
    """Der klassische Fall 'PMI sinkt + Inflation hoch' (z. B. 2022) muss
    STAGFLATION ergeben — die alte Logik matchte hier fälschlich SLOWDOWN."""
    regime = ft.detect_regime(pmi=47.0, pmi_trend=-2.0, cli=1.0, cpi=6.5)
    assert regime == ft.REGIME_STAGFLATION


def test_slowdown_when_growth_weak_and_inflation_low():
    assert (
        ft.detect_regime(pmi=47.0, pmi_trend=-1.0, cli=1.0, cpi=2.0)
        == ft.REGIME_SLOWDOWN
    )


def test_goldilocks_and_heating_up():
    assert (
        ft.detect_regime(pmi=55.0, pmi_trend=1.0, cli=1.5, cpi=2.0)
        == ft.REGIME_GOLDILOCKS
    )
    # Starkes Wachstum + hohe Inflation → HEATING UP.
    assert (
        ft.detect_regime(pmi=55.0, pmi_trend=1.0, cli=1.5, cpi=4.5)
        == ft.REGIME_HEATING_UP
    )
    # Spätzyklische Mischlage (Trend negativ) → HEATING UP statt Goldilocks.
    assert (
        ft.detect_regime(pmi=55.0, pmi_trend=-1.0, cli=1.5, cpi=2.0)
        == ft.REGIME_HEATING_UP
    )


def test_negative_cli_forces_weak_growth():
    assert (
        ft.detect_regime(pmi=53.0, pmi_trend=0.5, cli=-1.0, cpi=2.0)
        == ft.REGIME_SLOWDOWN
    )


def test_pmi_hysteresis_band_keeps_previous_assessment():
    """Im Band 49–51 hält das vorherige Regime die Wachstums-Einstufung —
    kein Flackern um die 50er-Marke."""
    # PMI 50,4: mit Slowdown-Vorgeschichte weiter schwach …
    assert (
        ft.detect_regime(50.4, 0.5, 1.0, 2.0, prev_regime=ft.REGIME_SLOWDOWN)
        == ft.REGIME_SLOWDOWN
    )
    # … mit Goldilocks-Vorgeschichte weiter stark.
    assert (
        ft.detect_regime(50.4, 0.5, 1.0, 2.0, prev_regime=ft.REGIME_GOLDILOCKS)
        == ft.REGIME_GOLDILOCKS
    )
    # Ohne Vorgeschichte entscheidet die 50er-Marke.
    assert ft.detect_regime(49.6, 0.5, 1.0, 2.0) == ft.REGIME_SLOWDOWN
    # Außerhalb des Bands übersteuert der PMI die Vorgeschichte.
    assert (
        ft.detect_regime(47.0, 0.5, 1.0, 2.0, prev_regime=ft.REGIME_GOLDILOCKS)
        == ft.REGIME_SLOWDOWN
    )


def test_inverted_curve_downgrades_goldilocks():
    assert (
        ft.detect_regime(55.0, 1.0, 1.5, 2.0, spread=-0.4)
        == ft.REGIME_HEATING_UP
    )
    assert (
        ft.detect_regime(55.0, 1.0, 1.5, 2.0, spread=0.4)
        == ft.REGIME_GOLDILOCKS
    )


# ── Tilts & Zerlegung ──────────────────────────────────────────────────────


def test_sentiment_tilts_symmetric():
    # Risk-off: Low Vol + Quality profitieren.
    tilts, fired = ft.sentiment_tilts(vix=31.0, credit=550.0, pcr=1.0)
    assert tilts["Low Volatility"] == pytest.approx(0.02)
    assert tilts["Quality"] == pytest.approx(0.02)  # VIX +1pp und Credit +1pp
    assert tilts["Value"] == pytest.approx(-0.01)
    assert len(fired) == 2

    # Risk-on: Momentum profitiert, Low Vol gibt ab.
    tilts, fired = ft.sentiment_tilts(vix=12.0, credit=300.0, pcr=1.0)
    assert tilts["Momentum"] == pytest.approx(0.01)
    assert tilts["Low Volatility"] == pytest.approx(-0.01)
    assert len(fired) == 1

    # Put/Call-Kontra-Signale.
    tilts, _ = ft.sentiment_tilts(vix=20.0, credit=300.0, pcr=1.3)
    assert tilts["Momentum"] == pytest.approx(0.01)
    tilts, _ = ft.sentiment_tilts(vix=20.0, credit=300.0, pcr=0.6)
    assert tilts["Low Volatility"] == pytest.approx(0.01)


def test_tactical_weights_decomposition_sums():
    strategic = {f: 0.2 for f in ft.FACTORS}
    momenta = {
        "Value": 5.0,
        "Quality": 4.0,
        "Growth": 3.0,
        "Momentum": 2.0,
        "Low Volatility": 1.0,
    }
    mom_signal = ft.momentum_signal(momenta)
    sent, _ = ft.sentiment_tilts(vix=31.0, credit=300.0)
    decomp = ft.tactical_weights(strategic, ft.REGIME_SLOWDOWN, mom_signal, sent)

    total = sum(d["tactical"] for d in decomp.values())
    assert total == pytest.approx(1.0)
    # Zerlegung exakt nachvollziehbar: tactical = clamp(Summe der Teile),
    # renormalisiert über alle Faktoren.
    clamped = {
        f: max(
            ft.WEIGHT_FLOOR,
            min(
                ft.WEIGHT_CAP,
                d["strategic"] + d["regime_tilt"] + d["momentum_tilt"] + d["sentiment_tilt"],
            ),
        )
        for f, d in decomp.items()
    }
    clamped_total = sum(clamped.values())
    for f, d in decomp.items():
        assert d["tactical"] == pytest.approx(clamped[f] / clamped_total)
    # Top-2-Momentum übergewichtet, Bottom-2 untergewichtet.
    assert decomp["Value"]["momentum_tilt"] == pytest.approx(ft.MOMENTUM_TILT)
    assert decomp["Low Volatility"]["momentum_tilt"] == pytest.approx(-ft.MOMENTUM_TILT)


# ── Auto-Signale aus dem Universum ─────────────────────────────────────────


def _universe(n: int = 50) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    df = pd.DataFrame(
        {
            "value_score": np.linspace(0, 100, n),
            "quality_score": rng.uniform(0, 100, n),
            "growth_score": rng.uniform(0, 100, n),
            "momentum_score": rng.uniform(0, 100, n),
            "lowvol_score": rng.uniform(0, 100, n),
            # 6M-Return steigt mit dem Value-Score → positiver Value-Spread.
            "ret_6m": np.linspace(-0.10, 0.30, n),
            "pe": np.linspace(30.0, 8.0, n),  # hoher Value-Score = niedriges P/E
        }
    )
    return df


def test_factor_momentum_from_universe():
    vals = ft.factor_momentum_from_universe(_universe())
    # Value-Score korreliert per Konstruktion perfekt mit ret_6m →
    # deutlich positiver Spread in Prozentpunkten.
    assert vals["Value"] > 20.0
    assert set(vals) == set(ft.FACTORS)


def test_factor_momentum_requires_min_universe():
    assert ft.factor_momentum_from_universe(_universe(n=10)) == {}
    assert ft.factor_momentum_from_universe(None) == {}


def test_value_spread():
    vs = ft.value_spread(_universe())
    # Top-Value-Quintil hat per Konstruktion die niedrigsten P/E → Ratio < 1.
    assert vs is not None and vs < 1.0
    assert ft.value_spread(None) is None


# ── Ableitungen aus AV-Makro-Reihen ────────────────────────────────────────


def test_spread_from_yields_last_common_day():
    idx = pd.date_range("2026-08-01", periods=5, freq="D")
    y10 = pd.Series([4.0, 4.1, 4.2, 4.3, 4.4], index=idx)
    y2 = pd.Series([4.5, 4.4, 4.3, 4.2], index=idx[:4])  # letzter Tag fehlt
    # Letzter gemeinsamer Tag: 4.3 − 4.2 = 0.1.
    assert ft.spread_from_yields(y10, y2) == pytest.approx(0.10)
    assert ft.spread_from_yields(pd.Series(dtype=float), y2) is None


def test_cpi_yoy():
    idx = pd.date_range("2025-07-01", periods=14, freq="MS")
    series = pd.Series(np.linspace(300.0, 313.0, 14), index=idx)
    expected = (series.iloc[-1] / series.iloc[-13] - 1.0) * 100.0
    assert ft.cpi_yoy(series) == pytest.approx(round(expected, 1))
    # Zu kurze Reihe → None.
    assert ft.cpi_yoy(series.iloc[:12]) is None
