"""Tests für die Ex-ante-TE-Kontrolle (Spec 13, Tests 16–17)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.core.config import Settings
from app.core.diagnostics import SEV_ERROR, SEV_INFO, SEV_WARNING
from app.core.portfolio_construction import apply_te_constraint
from app.core.risk_mcte import compute_mcte


def _risk_cache(n_days: int = 600, bad_sigma: float = 0.03) -> dict:
    """Synthetisches Renditepanel: 5 Titel nahe an der Benchmark, ein
    Ausreißer ("BAD") mit hoher idiosynkratischer Volatilität."""
    rng = np.random.default_rng(42)
    idx = pd.bdate_range("2024-01-01", periods=n_days)
    bm = pd.Series(rng.normal(0.0003, 0.01, n_days), index=idx)
    cols = {}
    for i in range(4):
        cols[f"T{i}"] = bm + rng.normal(0, 0.002, n_days)
    cols["BAD"] = bm + rng.normal(0, bad_sigma, n_days)
    return {"returns": pd.DataFrame(cols, index=idx), "bm_returns": bm}


def _settings(**overrides) -> Settings:
    s = Settings()
    s.pc_weight_floor = 0.02
    s.pc_weight_cap = 0.50
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _weights() -> pd.Series:
    return pd.Series(0.2, index=["T0", "T1", "T2", "T3", "BAD"])


def test_te_constraint():
    """TE > max wird reduziert; CTE-Anteil > 15 % wird reduziert;
    Nichterfüllbarkeit erzeugt Fehler-Diagnose ohne Abbruch; Σ CTE = TE
    (Test 16)."""
    cache = _risk_cache()
    s = _settings(pc_te_max=0.06, pc_max_cte_share=0.90)

    w0 = _weights()
    w1, te1, details1 = apply_te_constraint(w0.copy(), s, cache)
    # Der Vola-Ausreißer treibt den TE — sein Gewicht wird abgebaut.
    assert details1["iterations"] > 0
    assert w1["BAD"] < w0["BAD"]
    assert w1.sum() == pytest.approx(1.0, abs=1e-9)
    assert te1 is not None

    # Σ CTE = TE bleibt erhalten (Invariante des Risikomoduls).
    res = compute_mcte(cache["returns"], cache["bm_returns"], w1.to_dict())
    assert res.ranking["cte"].sum() == pytest.approx(res.te_ledoit_wolf, rel=1e-6)

    # CTE-Anteils-Restriktion: TE-Limit großzügig, Anteil eng.
    s2 = _settings(pc_te_max=10.0, pc_max_cte_share=0.15)
    w2, _, details2 = apply_te_constraint(_weights(), s2, cache)
    assert details2["iterations"] > 0
    assert w2["BAD"] < 0.2

    # Unerfüllbare Restriktion → Fehler-Diagnose, letzter Zustand bleibt.
    s3 = _settings(pc_te_max=1e-6)
    w3, te3, details3 = apply_te_constraint(_weights(), s3, cache)
    assert any(
        d.code == "te_constraint_unmet" and d.severity == SEV_ERROR
        for d in details3["diagnostics"]
    )
    assert w3.sum() == pytest.approx(1.0, abs=1e-9)
    assert te3 is not None and te3 > s3.pc_te_max

    # TE unter dem Zielband → Info-Diagnose, keine Aktion.
    calm = _risk_cache(bad_sigma=0.002)
    # Anteils-Schranke lockern: mit 5 Titeln liegt der maximale CTE-Anteil
    # strukturell bei ≥ 1/5.
    s4 = _settings(pc_te_target_low=0.5, pc_te_max=10.0, pc_max_cte_share=0.9)
    w4, _, details4 = apply_te_constraint(_weights(), s4, calm)
    pd.testing.assert_series_equal(w4, _weights())
    assert any(
        d.code == "te_below_target" and d.severity == SEV_INFO
        for d in details4["diagnostics"]
    )


def test_te_skipped_low_coverage():
    """Abdeckung < 60 % → TE-Schritt übersprungen, Gewichte unverändert
    (Test 17)."""
    cache = _risk_cache()
    # Nur 40 % des Portfoliogewichts haben Kursdaten.
    w = pd.Series(
        {"T0": 0.2, "T1": 0.2, "OHNE1": 0.3, "OHNE2": 0.3}
    )
    s = _settings()
    out, te, details = apply_te_constraint(w.copy(), s, cache)
    pd.testing.assert_series_equal(out, w)
    assert te is None
    assert details["coverage"] == pytest.approx(0.4)
    assert any(
        d.code == "te_skipped_low_coverage" and d.severity == SEV_WARNING
        for d in details["diagnostics"]
    )

    # Ganz ohne Kurs-Cache ebenfalls Skip mit Warnung.
    out2, te2, details2 = apply_te_constraint(w.copy(), s, None)
    pd.testing.assert_series_equal(out2, w)
    assert te2 is None
    assert any(
        d.code == "te_skipped_no_data" for d in details2["diagnostics"]
    )
