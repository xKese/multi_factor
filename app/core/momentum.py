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
