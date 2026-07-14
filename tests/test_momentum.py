"""Tests für die Trend-Phasen-Klassifikation des Momentum-Monitors."""

from __future__ import annotations

import numpy as np

from app.core.momentum import (
    PHASE_ESTABLISHED_BEAR,
    PHASE_ESTABLISHED_BULL,
    PHASE_FRESH_BEAR,
    PHASE_FRESH_BULL,
    PHASE_NEUTRAL,
    PHASE_NONE,
    PHASE_TIRED_BEAR,
    PHASE_TIRED_BULL,
    classify_trend_phase,
)


def test_fresh_bullish_small_gap():
    # Gap 1 % ≤ 3 %-Schwelle, Kurs über beiden SMAs, Return unauffällig.
    assert classify_trend_phase(102, 100, 99, 0.01) == PHASE_FRESH_BULL


def test_established_bullish_large_gap():
    assert classify_trend_phase(120, 115, 100, 0.05) == PHASE_ESTABLISHED_BULL


def test_tired_bullish_price_below_sma50():
    # Golden-Struktur, aber Kurs unter SMA-50 → Ermüdet.
    assert classify_trend_phase(105, 110, 100, 0.01) == PHASE_TIRED_BULL


def test_tired_bullish_negative_ret_1m():
    assert classify_trend_phase(120, 115, 100, -0.05) == PHASE_TIRED_BULL


def test_tired_beats_fresh():
    # Frisches Cross (Gap 0,5 %), aber der 1M-Return kippt → Warnung schlägt.
    assert classify_trend_phase(102, 100.5, 100, -0.5) == PHASE_TIRED_BULL


def test_fresh_bullish_without_ret_1m():
    # Ohne 1M-Return entscheidet nur Kurs vs. SMA-50.
    assert classify_trend_phase(101, 100, 99, None) == PHASE_FRESH_BULL
    assert classify_trend_phase(101, 100, 99, np.nan) == PHASE_FRESH_BULL


def test_bearish_mirror():
    assert classify_trend_phase(98, 99, 100, -0.01) == PHASE_FRESH_BEAR
    assert classify_trend_phase(80, 85, 100, -0.01) == PHASE_ESTABLISHED_BEAR
    # Kurs über SMA-50 gegen den Abwärtstrend → Ermüdet bearish.
    assert classify_trend_phase(90, 85, 100, 0.0) == PHASE_TIRED_BEAR
    # Positiver 1M-Return gegen den Abwärtstrend → Ermüdet bearish.
    assert classify_trend_phase(80, 85, 100, 0.05) == PHASE_TIRED_BEAR


def test_neutral_mixed_sides():
    # Kurs über SMA-200, aber SMA-50 darunter (bzw. umgekehrt) → Neutral.
    assert classify_trend_phase(105, 95, 100, 0.0) == PHASE_NEUTRAL
    assert classify_trend_phase(95, 105, 100, 0.0) == PHASE_NEUTRAL


def test_missing_inputs():
    assert classify_trend_phase(None, 100, 100, 0.0) == PHASE_NONE
    assert classify_trend_phase(100, np.nan, 100, 0.0) == PHASE_NONE
    assert classify_trend_phase(100, 100, None, 0.0) == PHASE_NONE
    assert classify_trend_phase(100, 100, 0, 0.0) == PHASE_NONE
