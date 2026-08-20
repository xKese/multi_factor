"""Tests für Ex-post-TE, Kernkennzahlen und aktive Sektorallokation.

Das Mini-Beispiel (3 Assets, 10 Renditetage) wurde manuell verifiziert:
Die Erwartungswerte unten sind unabhängig vom Modul mit der Definition
TE = Std(aktive Tagesrendite, ddof=1) × √252 vorgerechnet und als
Literale eingefroren.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core import risk_metrics as rm


def _fixture() -> tuple[pd.DataFrame, pd.Series]:
    """11 Preistage → 10 Renditen; 3 Assets + Benchmark."""

    idx = pd.bdate_range("2024-01-01", periods=11)
    prices = pd.DataFrame(
        {
            "A": [100, 101, 102, 101, 103, 104, 103, 105, 106, 107, 108],
            "B": [50, 50.5, 50, 51, 51.5, 51, 52, 52.5, 53, 52.5, 53.5],
            "C": [200, 199, 201, 203, 202, 204, 206, 205, 207, 209, 208],
        },
        index=idx,
    )
    benchmark = pd.Series(
        [1000, 1005, 1008, 1004, 1012, 1015, 1010, 1018, 1022, 1026, 1030],
        index=idx,
        dtype=float,
    )
    return prices, benchmark


WEIGHTS = {"A": 0.5, "B": 0.3, "C": 0.2}


def test_te_matches_hand_verified_fixture():
    """Pflichttest: TE und Kennzahlen gegen manuell vorgerechnete Werte."""

    prices, benchmark = _fixture()
    rets = rm.daily_returns(prices)
    pf = rm.portfolio_returns(rets, WEIGHTS)
    bm = rm.daily_returns(benchmark.to_frame("bm"))["bm"]

    metrics = rm.ex_post_metrics(pf, bm)

    assert metrics["te_gesamt"] == pytest.approx(0.04383171418341534, abs=1e-12)
    assert metrics["aktive_rendite_pa"] == pytest.approx(0.9487538515298743, abs=1e-12)
    assert metrics["information_ratio"] == pytest.approx(21.64537411335958, abs=1e-9)
    assert metrics["aktives_beta"] == pytest.approx(0.6433550099053346, abs=1e-12)
    assert metrics["korrelation"] == pytest.approx(0.7699830061748615, abs=1e-12)
    assert metrics["upside_capture"] == pytest.approx(1.5865975824476737, abs=1e-12)
    assert metrics["downside_capture"] == pytest.approx(-0.6884736283774326, abs=1e-12)
    assert metrics["max_rel_drawdown"] == pytest.approx(-9.43653861155136e-05, abs=1e-15)
    assert metrics["n_tage"] == 10
    # 10 Tage decken kein 1J-/3J-Fenster → NaN statt Scheingenauigkeit.
    assert np.isnan(metrics["te_1j"])
    assert np.isnan(metrics["te_3j"])


def test_variant_fixed_and_buyhold_differ_as_expected():
    """Variante A rebalanced täglich, Variante B lässt Gewichte driften —
    ab Tag 2 weichen die Renditen ab (handverifizierte Werte)."""

    prices, _ = _fixture()
    rets = rm.daily_returns(prices)

    fixed = rm.portfolio_returns(rets, WEIGHTS, rm.VARIANT_FIXED)
    buyhold = rm.portfolio_returns(rets, WEIGHTS, rm.VARIANT_BUYHOLD)

    # Tag 1 identisch (noch keine Drift), danach nicht mehr.
    assert fixed.iloc[1] == pytest.approx(buyhold.iloc[1], abs=1e-15)
    assert fixed.iloc[2] == pytest.approx(0.003990248271058272, abs=1e-15)
    assert buyhold.iloc[2] == pytest.approx(0.003972194637537285, abs=1e-15)
    assert not np.allclose(fixed.iloc[2:], buyhold.iloc[2:])


def test_unknown_variant_raises():
    prices, _ = _fixture()
    with pytest.raises(ValueError):
        rm.portfolio_returns(rm.daily_returns(prices), WEIGHTS, "monatlich")


def test_fixed_variant_renormalizes_over_missing_titles():
    """Ein Datenloch bei einem Titel darf nicht wie eine Nullrendite wirken:
    Die Gewichte werden an dem Tag über die verfügbaren Titel renormiert."""

    idx = pd.bdate_range("2024-01-01", periods=3)
    rets = pd.DataFrame(
        {"A": [0.01, np.nan, 0.01], "B": [0.03, 0.02, 0.03]}, index=idx
    )
    pf = rm.portfolio_returns(rets, {"A": 0.5, "B": 0.5})
    assert pf.iloc[0] == pytest.approx(0.02)
    assert pf.iloc[1] == pytest.approx(0.02)  # nur B verfügbar → 100 % B


def test_rolling_te_needs_window_coverage():
    active = pd.Series(
        np.random.default_rng(1).normal(0, 0.001, 300),
        index=pd.bdate_range("2023-01-02", periods=300),
    )
    roll = rm.rolling_te(active, 252)
    assert roll.iloc[:200].isna().all()
    assert roll.iloc[-1] == pytest.approx(
        active.tail(252).std(ddof=1) * np.sqrt(252)
    )


def test_max_relative_drawdown_on_constructed_path():
    """Pf verliert gegenüber Bm zwei Tage à −1 %/Tag relativ: rel. Drawdown
    ≈ 1 − (0,99)² (leichte Abweichung durch einfache Renditen)."""

    idx = pd.bdate_range("2024-01-01", periods=5)
    bm = pd.Series([0.0, 0.0, 0.0, 0.0, 0.0], index=idx)
    pf = pd.Series([0.02, -0.01, -0.01, 0.0, 0.03], index=idx)
    dd = rm.max_relative_drawdown(pf, bm)
    assert dd == pytest.approx(0.99 * 0.99 - 1.0, abs=1e-12)


def test_active_sector_weights_sorted_with_one_sided_sectors():
    weights = {"AAA": 0.5, "BBB": 0.3, "CCC": 0.2}
    sectors = {"AAA": "Information Technology", "BBB": "Financials"}
    bm = {"Information Technology": 0.28, "Financials": 0.16, "Energy": 0.04}

    df = rm.active_sector_weights(weights, sectors, bm)

    by_sector = df.set_index("sektor")
    # CCC ohne Sektor → "Unbekannt" mit bm 0; Energy nur im Benchmark.
    assert by_sector.loc["Unbekannt", "pf_gewicht"] == pytest.approx(0.2)
    assert by_sector.loc["Unbekannt", "bm_gewicht"] == 0.0
    assert by_sector.loc["Energy", "pf_gewicht"] == 0.0
    assert by_sector.loc["Energy", "aktiv"] == pytest.approx(-0.04)
    assert by_sector.loc["Information Technology", "aktiv"] == pytest.approx(0.22)
    # Sortierung nach |aktiv| absteigend.
    assert list(df["aktiv"].abs()) == sorted(df["aktiv"].abs(), reverse=True)
    # Portfolioseite summiert auf 1,0.
    assert df["pf_gewicht"].sum() == pytest.approx(1.0)
