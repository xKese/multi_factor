"""Zentraler Anzeige-Kontext: welches Scoring (v1/v2) ist primär?

Kleine, reine Funktionen, die den Seiten sagen, welche Spalten und Labels
die Primäranzeige tragen. ``settings.scoring_version`` steuert die Anzeige;
v1 bleibt als „Scoring v1 (Vergleich)" verfügbar (Accordion-Muster).
"""

from __future__ import annotations

from typing import Any

import pandas as pd


ZONE_TONES: dict[str, str | None] = {
    "KANDIDAT": "up",
    "HALTEN": "warn",
    "VERKAUFEN": "down",
    "FILTER": None,
}

# Klassen-Kurzcodes → CSS-Suffix der Design-Prototyp-Pillen (a/bp/b/c/d/f).
_CLASS_CLS = {"A": "a", "B+": "bp", "B": "b", "C": "c", "D": "d", "F": "f"}

_CLASS_LABELS = {
    "A": "Exzellent",
    "B+": "Sehr Gut",
    "B": "Gut",
    "C": "Durchschnitt",
    "D": "Unterdurchschnitt",
    "F": "Schwach",
}


def is_v2(df: pd.DataFrame | None = None) -> bool:
    """True, wenn Composite v2 die Primäranzeige ist und Daten vorliegen."""
    from app.core.state import STATE

    frame = STATE.scored if df is None else df
    return (
        STATE.settings.scoring_version == "v2"
        and "composite_score" in getattr(frame, "columns", [])
    )


def primary_cols(df: pd.DataFrame | None = None) -> dict[str, str | None]:
    """Spalten-/Label-Mapping der Primäranzeige (v1 oder v2)."""
    if is_v2(df):
        return {
            "version": "v2",
            "score": "composite_score",
            "score_label": "Composite (v2)",
            "class": "classification_v2",
            "zone": "zone_v2",
            "rec": None,
            "coverage": "data_coverage_v2",
        }
    return {
        "version": "v1",
        "score": "total_score",
        "score_label": "Gesamt-Score",
        "class": "classification",
        "zone": None,
        "rec": "recommendation",
        "coverage": "data_coverage",
    }


def class_of_score(score: Any) -> dict:
    """Score (0–100) → {code, label, cls} — v1- und v2-kompatibel.

    Bei v2 entsprechen die Schwellen ``classify_v2`` (Perzentile ·100:
    90/80/66,7/50/33), bei v1 dem Design-Prototyp (80/70/60/50/40) —
    identische Struktur, Version über :func:`is_v2` gewählt.
    """
    if score is None or pd.isna(score):
        return {"code": "–", "label": "Keine Daten", "cls": "f"}
    v = float(score)
    thresholds = (
        ((90, "A"), (80, "B+"), (66.7, "B"), (50, "C"), (33, "D"))
        if is_v2()
        else ((80, "A"), (70, "B+"), (60, "B"), (50, "C"), (40, "D"))
    )
    code = "F"
    for limit, c in thresholds:
        if v >= limit:
            code = c
            break
    return {"code": code, "label": _CLASS_LABELS[code], "cls": _CLASS_CLS[code]}


def class_color(value: Any) -> str | None:
    """Farbe einer Klassifikation — akzeptiert v1-Langform und v2-Kurzform."""
    from app.pages.common import SCORE_COLORS, SCORE_COLORS_V2

    key = str(value or "")
    return SCORE_COLORS.get(key) or SCORE_COLORS_V2.get(key)


def zone_tone(zone: Any) -> str | None:
    """Ton (up/warn/down/None) für eine v2-Zone."""
    return ZONE_TONES.get(str(zone or ""))
