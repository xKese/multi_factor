"""Tests für Szenario-Replay und Faktor-Schocks."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.core import risk_scenarios as rs


def _replay_panel():
    """3 Titel: A/B mit voller Historie, C erst ab Fenstermitte (IPO)."""

    idx = pd.bdate_range("2020-02-03", periods=40)
    a = pd.Series(np.linspace(100, 90, 40), index=idx)  # −10 % linear
    b = pd.Series(np.linspace(50, 47.5, 40), index=idx)  # −5 %
    c = pd.Series(np.nan, index=idx)
    c.iloc[25:] = np.linspace(30, 29, 15)  # zu wenig Fensterabdeckung
    prices = pd.DataFrame({"A": a, "B": b, "C": c})
    bm = pd.Series(np.linspace(1000, 920, 40), index=idx)  # −8 %
    return prices, bm


def test_replay_renormalizes_and_reports_coverage():
    """Pflichttest: Renormalisierung + Abdeckungsgrad. C (Gewicht 30 %) hat
    keine Fensterhistorie → Abdeckung 70 %, Gewichte A/B renormalisiert."""

    prices, bm = _replay_panel()
    weights = {"A": 0.5, "B": 0.2, "C": 0.3}

    result = rs.replay_scenario(
        prices, bm, weights, "Test", date(2020, 2, 3), date(2020, 3, 27)
    )

    assert result.coverage == pytest.approx(0.7)
    assert result.belastbar is True
    assert result.fehlende == ["C"]
    assert result.n_verfuegbar == 2
    # Renormalisierte Gewichte 5/7 bzw. 2/7 → kumulierte Rendite dazwischen.
    expected = 5 / 7 * (-0.10) + 2 / 7 * (-0.05)
    assert result.pf_rendite == pytest.approx(expected, abs=0.002)
    assert result.bm_rendite == pytest.approx(-0.08, abs=1e-9)
    assert result.aktiv == pytest.approx(result.pf_rendite - result.bm_rendite)
    assert result.max_drawdown <= result.pf_rendite  # monoton fallender Pfad
    assert result.schlechtester_tag is not None


def test_replay_below_60_percent_marked_not_reliable():
    """Pflichttest: Unter 60 % Abdeckung → „nicht belastbar"."""

    prices, bm = _replay_panel()
    weights = {"A": 0.3, "B": 0.2, "C": 0.5}  # C fehlt → Abdeckung 50 %

    result = rs.replay_scenario(
        prices, bm, weights, "Test", date(2020, 2, 3), date(2020, 3, 27)
    )

    assert result.coverage == pytest.approx(0.5)
    assert result.belastbar is False
    # Kennzahlen werden trotzdem gerechnet (transparent, aber markiert).
    assert not np.isnan(result.pf_rendite)


def test_replay_without_benchmark_history():
    prices, bm = _replay_panel()
    result = rs.replay_scenario(
        prices, bm, {"A": 1.0}, "GFC", date(2007, 10, 9), date(2009, 3, 9)
    )
    assert result.belastbar is False
    assert result.coverage == 0.0
    assert "Benchmark" in result.hinweis


def test_estimate_betas_recovers_synthetic_loadings():
    """Konstruierte Wochenrenditen = 1,2·Markt + 0,0004·Zins_bp − 0,1·Öl
    + 0,3·USD → die Regression muss die Betas (nahezu) exakt liefern."""

    rng = np.random.default_rng(3)
    n = 156
    idx = pd.date_range("2021-01-08", periods=n, freq="W-FRI")
    factors = pd.DataFrame(
        {
            "markt": rng.normal(0.002, 0.02, n),
            "zins_bp": rng.normal(0.0, 8.0, n),
            "oel": rng.normal(0.0, 0.03, n),
            "usd": rng.normal(0.0, 0.01, n),
        },
        index=idx,
    )
    y = (
        1.2 * factors["markt"]
        + 0.0004 * factors["zins_bp"]
        - 0.1 * factors["oel"]
        + 0.3 * factors["usd"]
    )
    weekly = pd.DataFrame({"SYN": y})

    betas = rs.estimate_betas(weekly, factors)

    row = betas.iloc[0]
    assert row["beta_markt"] == pytest.approx(1.2, abs=1e-9)
    assert row["beta_zins_bp"] == pytest.approx(0.0004, abs=1e-12)
    assert row["beta_oel"] == pytest.approx(-0.1, abs=1e-9)
    assert row["beta_usd"] == pytest.approx(0.3, abs=1e-9)
    assert row["r2"] == pytest.approx(1.0, abs=1e-12)
    assert not row["geringe_guete"]


def test_estimate_betas_flags_low_r2_and_short_history():
    rng = np.random.default_rng(5)
    n = 156
    idx = pd.date_range("2021-01-08", periods=n, freq="W-FRI")
    factors = pd.DataFrame(
        {
            "markt": rng.normal(0.0, 0.02, n),
            "zins_bp": rng.normal(0.0, 8.0, n),
            "oel": rng.normal(0.0, 0.03, n),
            "usd": rng.normal(0.0, 0.01, n),
        },
        index=idx,
    )
    weekly = pd.DataFrame(
        {
            "NOISE": rng.normal(0.0, 0.05, n),  # unkorreliert → R² ≈ 0
            "SHORT": np.concatenate([np.full(n - 20, np.nan), rng.normal(0, 0.02, 20)]),
        },
        index=idx,
    )

    betas = rs.estimate_betas(weekly, factors).set_index("ticker")

    assert betas.loc["NOISE", "r2"] < rs.MIN_R2
    assert bool(betas.loc["NOISE", "geringe_guete"])
    assert np.isnan(betas.loc["SHORT", "beta_markt"])
    assert bool(betas.loc["SHORT", "geringe_guete"])
    assert betas.loc["SHORT", "n_obs"] == 20


def test_apply_shocks_propagates_and_reports_active_effect():
    """Schock-Arithmetik inkl. Kombi-Szenario „Stagflation" gegen von Hand
    gerechnete Werte; Benchmark reagiert mit Beta 1 nur auf den Markt."""

    betas = pd.DataFrame(
        [
            {
                "ticker": "A",
                "beta_markt": 1.0,
                "beta_zins_bp": -0.0005,
                "beta_oel": 0.1,
                "beta_usd": 0.0,
                "r2": 0.6,
                "geringe_guete": False,
                "n_obs": 156,
            },
            {
                "ticker": "B",
                "beta_markt": 0.5,
                "beta_zins_bp": 0.0,
                "beta_oel": -0.2,
                "beta_usd": 0.5,
                "r2": 0.1,
                "geringe_guete": True,
                "n_obs": 156,
            },
        ]
    )
    weights = {"A": 0.6, "B": 0.4}
    shocks = {
        "Zinsen +100bp": {"zins_bp": 100.0},
        "Stagflation": {"markt": -0.10, "zins_bp": 75.0, "oel": 0.25},
    }

    out = rs.apply_shocks(betas, weights, shocks).set_index("szenario")

    # Zinsen +100bp: A: −0,0005·100 = −5 %; B: 0 → Pf = 0,6·(−5 %) = −3 %.
    zins = out.loc["Zinsen +100bp"]
    assert zins["pf_pnl"] == pytest.approx(0.6 * -0.05, abs=1e-12)
    assert zins["bm_pnl"] == 0.0
    assert zins["aktiv"] == pytest.approx(0.6 * -0.05, abs=1e-12)

    # Stagflation: A: 1,0·(−0,10) + (−0,0005)·75 + 0,1·0,25 = −0,1125;
    # B: 0,5·(−0,10) + (−0,2)·0,25 = −0,10 → Pf = 0,6·A + 0,4·B = −0,1075.
    stag = out.loc["Stagflation"]
    assert stag["pf_pnl"] == pytest.approx(0.6 * -0.1125 + 0.4 * -0.10, abs=1e-12)
    assert stag["bm_pnl"] == pytest.approx(-0.10)
    assert stag["aktiv"] == pytest.approx(stag["pf_pnl"] - (-0.10), abs=1e-12)
    assert stag["abdeckung"] == pytest.approx(1.0)
    assert stag["n_geringe_guete"] == 1


def test_apply_shocks_excludes_titles_without_betas():
    betas = pd.DataFrame(
        [
            {
                "ticker": "A",
                "beta_markt": 1.0,
                "beta_zins_bp": 0.0,
                "beta_oel": 0.0,
                "beta_usd": 0.0,
                "r2": 0.5,
                "geringe_guete": False,
                "n_obs": 156,
            },
            {
                "ticker": "B",
                "beta_markt": float("nan"),
                "beta_zins_bp": float("nan"),
                "beta_oel": float("nan"),
                "beta_usd": float("nan"),
                "r2": float("nan"),
                "geringe_guete": True,
                "n_obs": 10,
            },
        ]
    )
    out = rs.apply_shocks(betas, {"A": 0.7, "B": 0.3}, {"Markt −15%": {"markt": -0.15}})
    row = out.iloc[0]
    assert row["abdeckung"] == pytest.approx(0.7)
    # Nur A trägt; renormalisiert wirkt A mit vollem Gewicht.
    assert row["pf_pnl"] == pytest.approx(-0.15)


def test_weekly_factor_panel_scales_yield_to_bp():
    idx = pd.bdate_range("2024-01-01", periods=15)
    prices = pd.DataFrame({"A": np.linspace(100, 110, 15)}, index=idx)
    bm = pd.Series(np.linspace(50, 55, 15), index=idx)
    macro = pd.DataFrame(
        {
            "y10": np.linspace(4.0, 4.14, 15),  # +0,01 pp je Tag → +5 bp je Woche
            "wti": np.linspace(70, 77, 15),
            "eurusd": np.linspace(1.10, 1.12, 15),
        },
        index=idx,
    )

    weekly, factors = rs.weekly_factor_panel(prices, bm, macro)

    diffs = factors["zins_bp"].dropna()
    assert len(diffs)
    assert diffs.iloc[-1] == pytest.approx(5.0, abs=1e-9)
    assert "markt" in factors.columns and "usd" in factors.columns
