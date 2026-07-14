"""Gemeinsamer Momentum-Classifier (Excel-Semantik).

Drei-Bedingungen-Regel fuer Golden / Death Cross gemaess Definition in
``TAA Conviction.xlsx``: Es reicht nicht, dass Kurs und SMA-50 ueber dem
SMA-200 liegen - der Kurs muss zusaetzlich ueber (bzw. unter) dem SMA-50
liegen.
"""

from __future__ import annotations

from typing import Any

import pandas as pd


MOMENTUM_GOLDEN = "Golden Cross"
MOMENTUM_UP = "Kurs > SMA-200"
MOMENTUM_DOWN = "Kurs < SMA-200"
MOMENTUM_DEATH = "Death Cross"
MOMENTUM_NONE = "-"

MOMENTUM_STATES = (MOMENTUM_GOLDEN, MOMENTUM_UP, MOMENTUM_DOWN, MOMENTUM_DEATH)

# ── Trend-Phasen (Momentum-Monitor) ─────────────────────────────────────────
#
# Die 4-State-Klassifikation oben ist zustandsbasiert: "Golden Cross" bleibt
# aktiv, solange der Kurs über beiden SMAs liegt — egal wie lange das Cross
# zurückliegt. Die Trend-Phase unterscheidet innerhalb des Regimes nach
# Reife: frisch (SMA-50/200-Abstand noch klein), etabliert, ermüdet (Kurs
# fällt unter SMA-50 bzw. 1M-Return dreht gegen den Trend).

PHASE_FRESH_BULL = "Frisch bullish"
PHASE_ESTABLISHED_BULL = "Etabliert bullish"
PHASE_TIRED_BULL = "Ermüdet bullish"
PHASE_NEUTRAL = "Neutral"
PHASE_TIRED_BEAR = "Ermüdet bearish"
PHASE_ESTABLISHED_BEAR = "Etabliert bearish"
PHASE_FRESH_BEAR = "Frisch bearish"
PHASE_NONE = "-"

TREND_PHASES = (
    PHASE_FRESH_BULL,
    PHASE_ESTABLISHED_BULL,
    PHASE_TIRED_BULL,
    PHASE_NEUTRAL,
    PHASE_TIRED_BEAR,
    PHASE_ESTABLISHED_BEAR,
    PHASE_FRESH_BEAR,
)

# |SMA-50 − SMA-200| / SMA-200 ≤ 3 % gilt als frisches Cross (identisch zur
# "Nahe am Kreuz"-Schwelle des Momentum-Monitors).
FRESH_GAP_THRESHOLD = 0.03
# Rausch-Puffer für den 1M-Return beim Ermüdungs-Kriterium.
TIRED_RET_1M = 0.02


def classify_trend_phase(
    price: Any, sma_50: Any, sma_200: Any, ret_1m: Any = None
) -> str:
    """Trend-Phase aus der SMA-Geometrie plus 1M-Return ableiten.

    Präzedenz innerhalb eines Regimes: Ermüdet vor Frisch vor Etabliert —
    ein frisches Cross, das bereits kippt, ist eine Warnung, kein Kaufsignal.
    ``ret_1m`` ist optional; fehlt es, entscheidet nur Kurs vs. SMA-50 über
    die Ermüdung.
    """
    if pd.isna(price) or pd.isna(sma_50) or pd.isna(sma_200) or sma_200 <= 0:
        return PHASE_NONE
    gap = (sma_50 - sma_200) / sma_200
    has_ret = ret_1m is not None and not pd.isna(ret_1m)

    if sma_50 > sma_200 and price > sma_200:
        if price < sma_50 or (has_ret and ret_1m < -TIRED_RET_1M):
            return PHASE_TIRED_BULL
        if gap <= FRESH_GAP_THRESHOLD:
            return PHASE_FRESH_BULL
        return PHASE_ESTABLISHED_BULL

    if sma_50 < sma_200 and price < sma_200:
        if price > sma_50 or (has_ret and ret_1m > TIRED_RET_1M):
            return PHASE_TIRED_BEAR
        if abs(gap) <= FRESH_GAP_THRESHOLD:
            return PHASE_FRESH_BEAR
        return PHASE_ESTABLISHED_BEAR

    return PHASE_NEUTRAL


def classify_momentum(price: Any, sma_50: Any, sma_200: Any) -> str:
    if pd.isna(price) or pd.isna(sma_50) or pd.isna(sma_200):
        return MOMENTUM_NONE
    if price > sma_200 and sma_50 > sma_200 and price > sma_50:
        return MOMENTUM_GOLDEN
    if price < sma_200 and sma_50 < sma_200 and price < sma_50:
        return MOMENTUM_DEATH
    if price > sma_200:
        return MOMENTUM_UP
    return MOMENTUM_DOWN
