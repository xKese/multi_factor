"""Smoke-Tests: Lädt Koyfin-CSV-Fixture und prüft plausible Outputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import _factor_score, _indicator_percentile, compute_scores


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

    # Momentum-Monitor-Spalten.
    for col in ["sma_gap", "mom_12_1", "dist_52w_high", "sma_20_distance", "trend_phase"]:
        assert col in scored.columns, col

    row = scored.dropna(subset=["ret_1m", "ret_12m"]).iloc[0]
    assert abs(row["mom_12_1"] - (row["ret_12m"] - row["ret_1m"])) < 1e-9

    from app.core.momentum import PHASE_NONE, TREND_PHASES

    assert scored["trend_phase"].isin([*TREND_PHASES, PHASE_NONE]).all()
    # Fixture ohne sma_20-Spalte → Distanz komplett NaN.
    assert scored["sma_20_distance"].isna().all()


def _universe(pe: list[float | None], pb: list[float | None]) -> pd.DataFrame:
    n = len(pe)
    return pd.DataFrame(
        {
            "ticker": [f"T{i}" for i in range(n)],
            "sector": ["Materials"] * n,
            "industry": ["Chemicals"] * n,
            "pe": pe,
            "pb": pb,
        }
    )


def test_lower_better_percentile_inverted():
    """Günstigster P/B der Industrie ⇒ hohes Perzentil, teuerster ⇒ niedriges."""
    df = _universe(
        pe=[10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
        pb=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
    )
    settings = Settings()
    assert len(df) >= settings.min_stocks_per_industry

    pct = _indicator_percentile(df, "pb", settings)
    assert pct.iloc[0] > 0.7, "günstigster P/B muss Top-Perzentil bekommen"
    assert pct.iloc[-1] < 0.3, "teuerster P/B muss schlechtes Perzentil bekommen"


def test_negative_multiple_gets_no_percentile():
    """Negativer P/E (Verlust) darf nicht als 'am günstigsten' ranken."""
    df = _universe(
        pe=[-5.0, 8.0, 12.0, 16.0, 20.0, 24.0],
        pb=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    )
    settings = Settings()

    pct = _indicator_percentile(df, "pe", settings)
    assert pd.isna(pct.iloc[0]), "negativer P/E muss NaN-Perzentil bekommen"
    assert pct.iloc[1] == pct.dropna().max(), "bester positiver P/E ist Top"

    # Faktor-Score bleibt berechenbar: der maskierte Wert wird über die
    # dynamische Neugewichtung ausgeklammert statt mit 0 einzugehen.
    score = _factor_score(df, {"pe": 0.5, "pb": 0.5}, settings)
    assert score.notna().all()
    pb_only = _indicator_percentile(df, "pb", settings)
    assert abs(score.iloc[0] - pb_only.iloc[0]) < 1e-9


def test_pdf_percentiles_mask_negative_multiples():
    """PDF-Export (Parallel-Implementierung) maskiert negative Multiples ebenso."""
    from app.core.factsheet_pdf import compute_indicator_percentiles
    from app.core.indicators import INDICATOR_GROUPS

    df = _universe(
        pe=[-5.0, 8.0, 12.0, 16.0, 20.0, 24.0],
        pb=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
    )
    value_group = next(g for g in INDICATOR_GROUPS if g.name == "Value")
    pct_map = compute_indicator_percentiles(df, value_group)

    assert pd.isna(pct_map["pe"].iloc[0])
    assert pct_map["pe"].iloc[1] == pct_map["pe"].dropna().max()
    assert pct_map["pb"].iloc[0] == pct_map["pb"].dropna().max()


if __name__ == "__main__":
    test_full_pipeline()
    test_lower_better_percentile_inverted()
    test_negative_multiple_gets_no_percentile()
    test_pdf_percentiles_mask_negative_multiples()
    print("OK")
