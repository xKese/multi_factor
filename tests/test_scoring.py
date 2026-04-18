"""Smoke-Tests: Lädt Koyfin-CSV-Fixture und prüft plausible Outputs."""

from __future__ import annotations

from pathlib import Path

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


def test_full_pipeline():
    df = load_koyfin_csv(FIXTURE.read_bytes())
    assert len(df) >= 10, "Erwarte mindestens 10 Aktien im Fixture"
    assert "ticker" in df.columns

    settings = Settings()
    scored = compute_scores(df, settings)

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

    piotr = scored["piotroski"].dropna()
    assert piotr.min() >= 0 and piotr.max() <= 9

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

    assert scored["recommendation"].isin(
        ["STRONG BUY", "BUY", "HOLD", "SELL", "Filter nicht bestanden", "-"]
    ).all()


if __name__ == "__main__":
    test_full_pipeline()
    print("OK")
