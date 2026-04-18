"""Smoke-Tests: Lädt echte Excel-Daten und prüft plausible Outputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.config import Settings
from app.core.data_loader import load_from_excel
from app.core.scoring import compute_scores


EXCEL = Path(__file__).resolve().parent.parent / "M&S_Multi-Faktor-Model.xlsx"


def test_full_pipeline():
    df = load_from_excel(EXCEL)
    assert len(df) > 500, "Erwarte > 500 Aktien im Universum"
    assert "ticker" in df.columns

    settings = Settings()
    scored = compute_scores(df, settings)

    # Score-Spalten vorhanden und in Range.
    for col in [
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
        "total_score",
    ]:
        assert col in scored.columns, col
        vals = scored[col].dropna()
        assert vals.min() >= 0 and vals.max() <= 100, f"{col} out of [0,100]"

    # Piotroski-Range.
    piotr = scored["piotroski"].dropna()
    assert piotr.min() >= 0 and piotr.max() <= 9

    # Klassifikation liefert erwartete Labels.
    assert scored["classification"].isin(
        [
            "A - Exzellent",
            "B+ - Sehr Gut",
            "B - Gut",
            "C - Durchschnitt",
            "D - Unterdurchschnitt",
            "F - Schwach",
            "-",
        ]
    ).all()

    # Empfehlung-Labels.
    assert scored["recommendation"].isin(
        ["STRONG BUY", "BUY", "HOLD", "SELL", "Filter nicht bestanden", "-"]
    ).all()


if __name__ == "__main__":
    test_full_pipeline()
    print("OK")
