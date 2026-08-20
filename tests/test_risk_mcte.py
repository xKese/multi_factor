"""Tests für das MCTE-Modul (Ex-ante TE, Risikobeiträge)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core import risk_mcte


def _panel(n_days: int = 400, seed: int = 7):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2023-01-02", periods=n_days)
    bm = pd.Series(rng.normal(0.0003, 0.01, n_days), index=idx)
    rets = pd.DataFrame(
        {
            "A": bm + rng.normal(0.0, 0.004, n_days),
            "B": bm + rng.normal(0.0002, 0.006, n_days),
            "C": bm + rng.normal(-0.0001, 0.008, n_days),
        },
        index=idx,
    )
    return rets, bm


WEIGHTS = {"A": 0.5, "B": 0.3, "C": 0.2}


def test_sum_cte_equals_te_for_both_estimators():
    """Pflichttest: Σ CTE_i == ex-ante TE mit Toleranz 1e-10 — für die
    Ledoit-Wolf- und die Sample-Kovarianz."""

    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)

    assert abs(result.ranking["cte"].sum() - result.te_ledoit_wolf) < 1e-10

    # Sample-Pfad direkt über die interne Beitragsrechnung prüfen.
    active = rets.sub(bm, axis=0).tail(risk_mcte.COV_WINDOW).dropna()
    w = np.array([WEIGHTS[c] for c in rets.columns])
    sigma = np.cov(active.to_numpy().T, ddof=1)
    te, _, cte = risk_mcte._contributions(sigma, w)
    assert abs(cte.sum() - te) < 1e-10
    assert te == pytest.approx(result.te_sample, rel=1e-12)


def test_both_te_estimates_close_on_well_conditioned_data():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)
    assert result.te_ledoit_wolf == pytest.approx(result.te_sample, rel=0.15)
    assert 0.0 <= result.shrinkage <= 1.0
    assert result.n_tage == 400


def test_ranking_sorted_and_in_basis_points():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)
    assert list(result.ranking["cte"]) == sorted(result.ranking["cte"], reverse=True)
    assert result.ranking["cte_bp"].iloc[0] == pytest.approx(
        result.ranking["cte"].iloc[0] * 10_000.0
    )


def test_single_title_edge_case():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets[["A"]], bm, {"A": 1.0})
    active = rets["A"].sub(bm).tail(risk_mcte.COV_WINDOW)
    # Bei einem Titel ist der TE (Sample) einfach Std × √252.
    assert result.te_sample == pytest.approx(
        active.std(ddof=1) * np.sqrt(252), rel=1e-10
    )
    assert abs(result.ranking["cte"].sum() - result.te_ledoit_wolf) < 1e-10


def test_short_history_title_excluded_and_reported():
    """Ein Titel mit < 60 % Abdeckung fliegt aus der Schätzung, wird aber
    samt Gewichtsanteil ausgewiesen; die übrigen Gewichte renormalisieren."""

    rets, bm = _panel()
    rets = rets.copy()
    rets.loc[rets.index[:300], "C"] = np.nan  # nur 100 von 400 Tagen

    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)

    assert result.ausgeschlossen == ["C"]
    assert result.ausgeschlossen_gewicht == pytest.approx(0.2)
    assert set(result.ranking["ticker"]) == {"A", "B"}
    assert result.ranking["gewicht"].sum() == pytest.approx(1.0)
    assert abs(result.ranking["cte"].sum() - result.te_ledoit_wolf) < 1e-10


def test_too_little_data_raises_german_message():
    idx = pd.bdate_range("2024-01-01", periods=10)
    rets = pd.DataFrame({"A": np.full(10, 0.001)}, index=idx)
    bm = pd.Series(np.zeros(10), index=idx)
    with pytest.raises(ValueError):
        risk_mcte.compute_mcte(rets, bm, {"A": 1.0})


def test_sector_aggregation_sums_to_te():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)
    agg = risk_mcte.aggregate_by_sector(
        result.ranking, {"A": "Tech", "B": "Tech", "C": None}
    )
    assert set(agg["sektor"]) == {"Tech", "Unbekannt"}
    assert agg["cte"].sum() == pytest.approx(result.te_ledoit_wolf, abs=1e-10)


def test_join_signals_merges_model_columns():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)
    scored = pd.DataFrame(
        [
            {
                "ticker": "A",
                "total_score": 72.5,
                "recommendation": "SELL",
                "sma_signal": "⚠ DEATH CROSS",
                "sector": "Technology",
            }
        ]
    )
    joined = risk_mcte.join_signals(result.ranking, scored)
    row = joined[joined["ticker"] == "A"].iloc[0]
    assert row["recommendation"] == "SELL"
    assert row["sma_signal"] == "⚠ DEATH CROSS"
    # Titel ohne Universums-Zeile behalten leere Signale.
    row_b = joined[joined["ticker"] == "B"].iloc[0]
    assert pd.isna(row_b["recommendation"])


def test_join_signals_with_empty_universe():
    rets, bm = _panel()
    result = risk_mcte.compute_mcte(rets, bm, WEIGHTS)
    joined = risk_mcte.join_signals(result.ranking, pd.DataFrame())
    assert "recommendation" in joined.columns
    assert joined["recommendation"].isna().all()
