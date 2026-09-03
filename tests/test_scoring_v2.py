"""Tests für Composite v2 (Spec 13, Tests 1–7a)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores
from app.core.scoring_v2 import (
    ZONE_CANDIDATE,
    ZONE_FILTER,
    ZONE_HOLD,
    ZONE_SELL,
    _clean_v2,
    assign_neutralization_group,
    assign_zones,
    composite_zscore,
    compute_scores_v2,
    derive_v2_indicators,
    factor_zscore,
    zscore_within_group,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


def _base_frame(n: int = 3, **overrides) -> pd.DataFrame:
    data = {
        "uid": [f"T{i}" for i in range(n)],
        "ticker": [f"T{i}" for i in range(n)],
        "sector": ["Information Technology"] * n,
        "industry": ["Software"] * n,
        "region": ["Europe"] * n,
        "revenue": [100.0] * n,
        "cogs": [40.0] * n,
        "total_assets": [200.0] * n,
        "total_assets_prev": [180.0] * n,
        "net_income": [20.0] * n,
        "ocf": [25.0] * n,
        "op_margin": [0.2] * n,
        "total_debt": [50.0] * n,
        "shares_out": [110.0] * n,
        "shares_out_prev": [100.0] * n,
        "pfcf": [20.0] * n,
        "ret_12m": [0.30] * n,
        "ret_1m": [0.05] * n,
        "volatility_1y": [0.25] * n,
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_derive_indicators():
    df = _base_frame(
        4,
        sector=[
            "Information Technology",
            "Financials",
            "Real Estate",
            "Information Technology",
        ],
        total_assets=[200.0, 200.0, 200.0, 0.0],
        total_debt=[50.0, 50.0, -5.0, 50.0],
        op_margin=[0.2, 0.2, 0.2, -0.1],
        volatility_1y=[0.25, np.nan, 0.02, 0.25],
    )
    out, diags = derive_v2_indicators(df, Settings())

    # Formeln aus Spec 1.3.
    assert out.loc[0, "gp_ta"] == pytest.approx((100 - 40) / 200)
    assert out.loc[0, "accruals"] == pytest.approx((20 - 25) / 200)
    assert out.loc[0, "ebit_proxy"] == pytest.approx(100 * 0.2)
    assert out.loc[0, "debt_ebit"] == pytest.approx(50 / 20)
    assert out.loc[0, "asset_growth"] == pytest.approx(200 / 180 - 1)
    assert out.loc[0, "share_issuance"] == pytest.approx(110 / 100 - 1)
    assert out.loc[0, "fcf_yield_calc"] == pytest.approx(1 / 20)
    assert out.loc[0, "mom_12_1"] == pytest.approx(0.25)
    assert out.loc[0, "mom_12_1_adj"] == pytest.approx(0.25 / 0.25)

    # Gültigkeitsbedingungen: total_assets ≤ 0 → NaN; ebit_proxy ≤ 0 → NaN.
    assert np.isnan(out.loc[3, "gp_ta"])
    assert np.isnan(out.loc[3, "accruals"])
    assert np.isnan(out.loc[3, "debt_ebit"])
    # total_debt ≤ 0 bei gültigem EBIT-Proxy → debt_ebit = 0.
    assert out.loc[2, "debt_ebit"] == 0.0

    # mom_12_1_adj: fehlende Vola → Fallback mom_12_1 (Info-Diagnose);
    # Vola < 0,05 → NaN (Gültigkeitsbedingung).
    assert out.loc[1, "mom_12_1_adj"] == pytest.approx(0.25)
    assert np.isnan(out.loc[2, "mom_12_1_adj"])
    assert any(d.code == "mom_vol_fallback" for d in diags)

    # Sektor-Flags.
    assert out["is_financial"].tolist() == [False, True, False, False]
    assert out["is_real_estate"].tolist() == [False, False, True, False]

    # Bereinigung: negative Multiples und Gültigkeitsbänder.
    clean_df = pd.DataFrame(
        {
            "ev_ebitda": [-3.0, 8.0],
            "roic": [2.5, 0.15],
            "accruals": [1.5, -0.2],
            "net_debt_ebitda": [60.0, -2.0],
        }
    )
    assert np.isnan(_clean_v2(clean_df, "ev_ebitda").iloc[0])
    assert _clean_v2(clean_df, "ev_ebitda").iloc[1] == 8.0
    assert np.isnan(_clean_v2(clean_df, "roic").iloc[0])
    assert np.isnan(_clean_v2(clean_df, "accruals").iloc[0])
    assert np.isnan(_clean_v2(clean_df, "net_debt_ebitda").iloc[0])
    # Nettocash (negativ) bleibt gültig.
    assert _clean_v2(clean_df, "net_debt_ebitda").iloc[1] == -2.0


def test_zscore_within_group():
    idx = range(12)
    series = pd.Series(
        [1.0, 2.0, 3.0, 4.0, 5.0, 100.0, np.nan, 5.0, 5.0, 5.0, 5.0, 5.0],
        index=idx,
    )
    groups = pd.Series(["a"] * 7 + ["b"] * 5, index=idx)

    z, degenerate = zscore_within_group(series, groups, direction=1.0)
    # NaN bleibt NaN.
    assert np.isnan(z.iloc[6])
    # Winsorisierung dämpft den Ausreißer, Cap ±3 hält alles im Band.
    assert z.abs().max() <= 3.0
    assert z.iloc[5] > 0
    # Gruppe b: std == 0 → z = 0 und Diagnose-Vermerk.
    assert (z.iloc[7:12] == 0).all()
    assert "b" in degenerate

    # Richtung −1 spiegelt die Werte.
    z_inv, _ = zscore_within_group(series, groups, direction=-1.0)
    pd.testing.assert_series_equal(z_inv.iloc[:6], -z.iloc[:6])

    # Weniger als 5 gültige Werte → z = 0.
    small = pd.Series([1.0, 2.0, 3.0], index=range(3))
    z_small, degenerate_small = zscore_within_group(
        small, pd.Series(["g"] * 3, index=range(3))
    )
    assert (z_small == 0).all()
    assert "g" in degenerate_small


def test_neutralization_fallback():
    n_a, n_b = 25, 10
    df = pd.DataFrame(
        {
            "region": ["A"] * n_a + ["B"] * n_b,
            "sector": ["Tech"] * (n_a + n_b),
        }
    )
    # Indikator 1: überall gültig → Region A hat 25 (region_sector),
    # Region B nur 10, aber Sektor global 35 → sector.
    valid_all = pd.Series(True, index=df.index)
    groups, level = assign_neutralization_group(df, valid_all, min_group_size=20)
    assert (level.iloc[:n_a] == "region_sector").all()
    assert (level.iloc[n_a:] == "sector").all()
    assert groups.iloc[0] == "rs:A|Tech"
    assert groups.iloc[n_a] == "sec:Tech"

    # Indikator 2: nur 6 gültige Werte → auch Sektorebene zu klein → global.
    valid_few = pd.Series([True] * 6 + [False] * (n_a + n_b - 6), index=df.index)
    groups2, level2 = assign_neutralization_group(df, valid_few, min_group_size=20)
    assert (level2 == "global").all()
    assert (groups2 == "__global__").all()


def test_factor_min_valid():
    df = pd.DataFrame(
        {
            "z_a": [0.5, 0.5, np.nan],
            "z_b": [1.5, np.nan, np.nan],
        }
    )
    factor, cov = factor_zscore(df, ["z_a", "z_b"], min_valid=2)
    assert factor.iloc[0] == pytest.approx(1.0)
    assert np.isnan(factor.iloc[1])
    assert np.isnan(factor.iloc[2])
    assert cov.tolist() == [1.0, 0.5, 0.0]

    # min_valid = 1: ein Indikator genügt.
    factor1, _ = factor_zscore(df, ["z_a", "z_b"], min_valid=1)
    assert factor1.iloc[1] == pytest.approx(0.5)


def test_composite_conditions():
    settings = Settings()
    weights = settings.v2_factor_weights()
    df = pd.DataFrame(
        {
            # Titel 0: alle Faktoren → voll gewichtet.
            # Titel 1: nur Momentum + Investment (0,40 < 0,70) → NaN.
            # Titel 2: Value + Quality + Momentum (0,85) → renormiert.
            # Titel 3-9: Füller für die globale Standardisierung.
            "z_value": [1.0, np.nan, 1.0] + [0.0] * 7,
            "z_quality": [1.0, np.nan, 1.0] + [0.0] * 7,
            "z_momentum": [1.0, 1.0, 1.0] + [0.0] * 7,
            "z_investment": [1.0, 1.0, np.nan] + [0.0] * 7,
        }
    )
    out, _ = composite_zscore(df.copy(), weights, min_factor_weight=0.7)
    assert out["composite_raw"].iloc[0] == pytest.approx(1.0)
    assert np.isnan(out["composite_raw"].iloc[1])
    # Renormierung: (0,3+0,3+0,25)·1 / 0,85 = 1.
    assert out["composite_raw"].iloc[2] == pytest.approx(1.0)

    # Weder Value noch Quality vorhanden → NaN, selbst wenn die
    # Gewichtssumme reicht (künstliche Gewichte).
    df_vq = pd.DataFrame(
        {
            "z_value": [np.nan] * 6,
            "z_quality": [np.nan] * 6,
            "z_momentum": [1.0, 0.5, 0.2, -0.1, -0.5, 0.0],
            "z_investment": [1.0, 0.5, 0.2, -0.1, -0.5, 0.0],
        }
    )
    out_vq, _ = composite_zscore(
        df_vq.copy(),
        {"value": 0.1, "quality": 0.1, "momentum": 0.5, "investment": 0.3},
        min_factor_weight=0.7,
    )
    assert out_vq["composite_raw"].isna().all()


def test_composite_deterministic():
    raw = load_koyfin_csv(str(FIXTURE))
    settings = Settings()
    scored = compute_scores(raw, settings)
    out1, _ = compute_scores_v2(scored, settings)
    out2, _ = compute_scores_v2(scored, settings)
    pd.testing.assert_frame_equal(out1, out2)


def test_v1_unchanged():
    raw = load_koyfin_csv(str(FIXTURE))
    settings = Settings()
    scored = compute_scores(raw, settings)
    snapshot = scored.copy(deep=True)
    compute_scores_v2(scored, settings)
    # v2 verändert das v1-Ergebnis nicht (arbeitet auf einer Kopie).
    pd.testing.assert_frame_equal(scored, snapshot)
    # Und v1 selbst bleibt deterministisch.
    pd.testing.assert_frame_equal(compute_scores(raw, settings), snapshot)


def test_fcf_yield_source():
    df = _base_frame(
        4,
        fcf_yield=[0.04, np.nan, -0.30, 0.70],
        pfcf=[20.0, 25.0, 10.0, 10.0],
    )
    out, _ = derive_v2_indicators(df, Settings())
    # Vorhandener FCF/EV wird verwendet.
    assert out.loc[0, "fcf_yield_v2"] == pytest.approx(0.04)
    assert out.loc[0, "fcf_yield_source"] == "ev"
    # NaN je Titel → Fallback 1/pfcf.
    assert out.loc[1, "fcf_yield_v2"] == pytest.approx(1 / 25)
    assert out.loc[1, "fcf_yield_source"] == "mcap"
    # Negativer FCF/EV bleibt gültig (echte Bewertungsinformation).
    assert out.loc[2, "fcf_yield_v2"] == pytest.approx(-0.30)
    assert out.loc[2, "fcf_yield_source"] == "ev"
    # Außerhalb [−0,5, 0,5] → NaN (Artefakt) → Fallback greift.
    assert out.loc[3, "fcf_yield_v2"] == pytest.approx(1 / 10)
    assert out.loc[3, "fcf_yield_source"] == "mcap"


def test_zones():
    settings = Settings()
    df = pd.DataFrame(
        {
            "composite_pct": [0.95, 0.75, 0.50, 0.85],
            "filter_pass": [True, True, True, False],
        }
    )
    zones = assign_zones(df, settings)
    assert zones.tolist() == [ZONE_CANDIDATE, ZONE_HOLD, ZONE_SELL, ZONE_FILTER]


def test_factor_timing_mode(monkeypatch):
    """Modus active: taktische Gewichte ersetzen Value/Quality/Momentum
    (Investment strategisch, renormiert); monitor: keine Wirkung (Spec 9)."""
    from app.core.scoring_v2 import map_tactical_to_v2

    settings = Settings()
    tactical = {
        "Value": 0.40, "Quality": 0.20, "Growth": 0.10,
        "Momentum": 0.20, "Low Volatility": 0.10,
    }
    mapped = map_tactical_to_v2(tactical, settings)
    assert mapped is not None
    total = 0.40 + 0.20 + 0.20 + settings.v2_weight_investment
    assert mapped["value"] == pytest.approx(0.40 / total)
    assert mapped["investment"] == pytest.approx(settings.v2_weight_investment / total)
    assert sum(mapped.values()) == pytest.approx(1.0)
    assert map_tactical_to_v2(None, settings) is None
    assert map_tactical_to_v2({"Value": 0.5}, settings) is None

    raw = load_koyfin_csv(str(FIXTURE))
    scored = compute_scores(raw, settings)

    # monitor (Default): tactical_weights werden ignoriert.
    settings.factor_timing_mode = "monitor"
    out_monitor, _ = compute_scores_v2(scored, settings, tactical_weights=mapped)
    out_plain, _ = compute_scores_v2(scored, settings)
    pd.testing.assert_series_equal(
        out_monitor["composite_raw"], out_plain["composite_raw"]
    )

    # active: Composite nutzt die gemappten Gewichte, Info-Diagnose vorhanden.
    settings.factor_timing_mode = "active"
    out_active, diags = compute_scores_v2(scored, settings, tactical_weights=mapped)
    assert any(d.code == "factor_timing_active" for d in diags)
    assert not out_active["composite_raw"].equals(out_plain["composite_raw"])
