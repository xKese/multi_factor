"""Tests für Selektion, Gewichtung und Overrides (Spec 13, Tests 10–15,
21–23)."""

from __future__ import annotations

import importlib
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.core import persistence
from app.core.config import Settings
from app.core.diagnostics import SEV_ERROR, SEV_INFO, SEV_WARNING
from app.core.portfolio_construction import (
    BenchmarkWeights,
    active_overrides,
    apply_overrides,
    compute_weights,
    load_benchmark_weights,
    override_expiry_diagnostics,
    select_portfolio,
)


def _fresh_db(tmp_path, monkeypatch):
    """Persistenz-Modul auf eine frische SQLite-Datei zeigen lassen."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    importlib.reload(persistence)
    return persistence


def _settings(**overrides) -> Settings:
    s = Settings()
    s.pc_target_n = 4
    s.pc_min_n = 2
    s.pc_max_n = 6
    s.pc_weight_floor = 0.02
    s.pc_weight_cap = 0.6
    s.pc_sector_band = 0.25
    s.pc_region_band = 0.9
    for key, value in overrides.items():
        setattr(s, key, value)
    return s


def _uni(rows: list[dict]) -> pd.DataFrame:
    base = {
        "sector": "A",
        "region": "Europe",
        "composite_z": 0.0,
        "composite_pct": 0.5,
        "zone_v2": "HALTEN",
        "volatility_1y": 0.25,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def _benchmark(sector=None, region=None) -> BenchmarkWeights:
    return BenchmarkWeights(sector=sector, region=region, diagnostics=[])


def test_buffer_zone():
    """HALTEN wird behalten, VERKAUFEN verkauft, Kandidat über Bandbreite
    übersprungen (Test 10)."""
    uni = _uni(
        [
            {"uid": "H1", "sector": "A", "zone_v2": "HALTEN", "composite_pct": 0.70},
            {"uid": "H2", "sector": "A", "zone_v2": "VERKAUFEN", "composite_pct": 0.30},
            {"uid": "C1", "sector": "B", "zone_v2": "KANDIDAT",
             "composite_pct": 0.90, "composite_z": 2.0},
            {"uid": "C2", "sector": "C", "zone_v2": "KANDIDAT",
             "composite_pct": 0.85, "composite_z": 1.5},
            {"uid": "C3", "sector": "B", "zone_v2": "KANDIDAT",
             "composite_pct": 0.90, "composite_z": 1.0},
        ]
    )
    bm = _benchmark(sector={"A": 0.3, "B": 0.6, "C": 0.0})
    res = select_portfolio(uni, {"H1": 0.5, "H2": 0.5}, bm, _settings())

    assert "H1" in res.portfolio.index
    assert "H2" not in res.portfolio.index
    assert res.exits["uid"].tolist() == ["H2"]
    assert res.exits["reason"].tolist() == ["zone_VERKAUFEN"]
    # C2 (Sektor C, Benchmark 0) würde die Bandbreite reißen → übersprungen.
    assert {"C1", "C3"} <= set(res.portfolio.index)
    assert "C2" not in res.portfolio.index
    assert any(
        s["uid"] == "C2" and s["reason"] == "sector_band" for s in res.skipped
    )


def test_sector_band_never_sells():
    """Bandbreitenverletzung durch gehaltene Titel erzeugt Warnung, keinen
    Verkauf (Test 11)."""
    uni = _uni(
        [
            {"uid": "H1", "sector": "A", "zone_v2": "HALTEN"},
            {"uid": "H2", "sector": "A", "zone_v2": "HALTEN"},
        ]
    )
    bm = _benchmark(sector={"A": 0.0, "B": 1.0})
    res = select_portfolio(uni, {"H1": 0.5, "H2": 0.5}, bm, _settings())

    assert set(res.portfolio.index) == {"H1", "H2"}
    assert res.exits.empty
    assert any(d.code == "band_violation_retained" for d in res.diagnostics)


def test_fill_zone():
    """Notfüllung greift nur unter pc_min_n, mit Warnung (Test 12)."""
    uni = _uni(
        [
            {"uid": "F1", "zone_v2": "HALTEN", "composite_pct": 0.75,
             "composite_z": 0.8},
            {"uid": "F2", "zone_v2": "HALTEN", "composite_pct": 0.72,
             "composite_z": 0.5},
            {"uid": "X1", "zone_v2": "HALTEN", "composite_pct": 0.50},
        ]
    )
    res = select_portfolio(uni, {}, _benchmark(), _settings())
    # Keine Kandidaten → Notfüllzone [0,70, 0,80) zieht F1/F2 nach.
    assert set(res.portfolio.index) == {"F1", "F2"}
    assert any(d.code == "fill_zone_used" for d in res.diagnostics)
    assert not any(d.code == "portfolio_below_min" for d in res.diagnostics)

    # Genug Kandidaten → keine Notfüllung.
    uni2 = _uni(
        [
            {"uid": f"K{i}", "zone_v2": "KANDIDAT", "composite_pct": 0.9,
             "composite_z": 1.0 + i * 0.1}
            for i in range(3)
        ]
        + [{"uid": "F1", "zone_v2": "HALTEN", "composite_pct": 0.75}]
    )
    res2 = select_portfolio(uni2, {}, _benchmark(), _settings())
    assert not any(d.code == "fill_zone_used" for d in res2.diagnostics)
    assert "F1" not in res2.portfolio.index

    # Reicht auch die Notfüllung nicht → Fehler-Diagnose, Ausgabe trotzdem.
    uni3 = _uni([{"uid": "X1", "zone_v2": "HALTEN", "composite_pct": 0.50}])
    res3 = select_portfolio(uni3, {}, _benchmark(), _settings())
    assert any(d.code == "portfolio_below_min" for d in res3.diagnostics)


def test_missing_benchmark_weights(monkeypatch):
    """Fehlende/veraltete Benchmark-Tabellen setzen die Restriktion aus,
    mit Warnung (Test 13; Quelle "static")."""
    monkeypatch.setattr(
        persistence, "load_region_weights", lambda: ({}, None)
    )
    s = _settings(risk_benchmark_sector_weights_asof="",
                  pc_benchmark_source="static")
    bm = load_benchmark_weights(s)
    assert bm.sector is None
    assert bm.region is None
    codes = {d.code for d in bm.diagnostics}
    assert "benchmark_sector_stale" in codes
    assert "benchmark_region_stale" in codes

    # Veralteter Sektor-Stand (> 120 Tage) → ebenfalls ausgesetzt.
    old = (date.today() - timedelta(days=200)).isoformat()
    bm_old = load_benchmark_weights(
        _settings(risk_benchmark_sector_weights_asof=old,
                  pc_benchmark_source="static")
    )
    assert bm_old.sector is None

    # Frische Stände → Restriktionen aktiv; unbekannte Region → Gewicht 0.
    monkeypatch.setattr(
        persistence,
        "load_region_weights",
        lambda: ({"Europe": 0.6, "North America": 0.4}, date.today()),
    )
    fresh = date.today().isoformat()
    bm_ok = load_benchmark_weights(
        _settings(risk_benchmark_sector_weights_asof=fresh,
                  pc_benchmark_source="static"),
        universe_regions=["Europe", "Mars"],
    )
    assert bm_ok.sector is not None
    assert bm_ok.region is not None
    assert bm_ok.region["Mars"] == 0.0
    assert any(d.code == "benchmark_region_unknown" for d in bm_ok.diagnostics)

    # Ohne Benchmark keine Band-Ablehnung: Kandidat in beliebigem Sektor
    # wird aufgenommen.
    uni = _uni(
        [
            {"uid": "C1", "sector": "Z", "zone_v2": "KANDIDAT",
             "composite_pct": 0.9, "composite_z": 1.0},
            {"uid": "C2", "sector": "Z", "zone_v2": "KANDIDAT",
             "composite_pct": 0.9, "composite_z": 0.9},
        ]
    )
    res = select_portfolio(uni, {}, bm, _settings())
    assert set(res.portfolio.index) == {"C1", "C2"}


def test_weights_cap_floor():
    """Summe 1, alle Gewichte in [floor, cap]; Inkonsistenz erkannt
    (Test 14)."""
    rng = np.random.default_rng(7)
    n = 30
    df = pd.DataFrame(
        {
            "composite_z": rng.normal(1.0, 1.0, n),
            "volatility_1y": rng.uniform(0.1, 0.6, n),
        },
        index=[f"T{i:02d}" for i in range(n)],
    )
    s = Settings()  # Default floor 0,02 / cap 0,05 ist für N=30 konsistent.
    diags: list = []
    w = compute_weights(df, s, diagnostics=diags)
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    assert (w >= s.pc_weight_floor - 1e-9).all()
    assert (w <= s.pc_weight_cap + 1e-9).all()
    assert not any(d.severity == SEV_ERROR for d in diags)

    # cap·N < 1 → Settings inkonsistent → Fehler-Diagnose.
    diags2: list = []
    compute_weights(df.iloc[:10], s, diagnostics=diags2)
    assert any(d.code == "weight_bounds_infeasible" for d in diags2)


def test_vol_fallback():
    """Fehlende Volatilität → Median, Diagnose-Info (Test 15)."""
    df = pd.DataFrame(
        {
            "composite_z": [1.0] * 30,
            "volatility_1y": [0.2] * 28 + [np.nan, np.nan],
        },
        index=[f"T{i:02d}" for i in range(30)],
    )
    diags: list = []
    w = compute_weights(df, Settings(), diagnostics=diags)
    assert w.sum() == pytest.approx(1.0, abs=1e-9)
    # Median = 0,2 → NaN-Titel bekommen dasselbe Rohgewicht wie alle anderen.
    assert w.nunique() == 1
    assert any(d.code == "weight_vol_fallback" for d in diags)


def test_override_validation(tmp_path, monkeypatch):
    """Fehlende Begründung/Owner/Ablauf oder Ablauf > 180 Tage wird
    abgelehnt (Test 21)."""
    p = _fresh_db(tmp_path, monkeypatch)
    ok_reason = "Regulatorischer Ausschluss nach Komitee-Beschluss."
    expires = date.today() + timedelta(days=90)

    with pytest.raises(ValueError):
        p.save_override("AAA", "exclude", "zu kurz", "Kevin", expires)
    with pytest.raises(ValueError):
        p.save_override("AAA", "exclude", ok_reason, "", expires)
    with pytest.raises(ValueError):
        p.save_override("AAA", "exclude", ok_reason, "Kevin", None)
    with pytest.raises(ValueError):
        p.save_override(
            "AAA", "exclude", ok_reason, "Kevin",
            date.today() + timedelta(days=200),
        )
    with pytest.raises(ValueError):
        p.save_override("AAA", "weight", ok_reason, "Kevin", expires)
    with pytest.raises(ValueError):
        p.save_override(
            "AAA", "include", ok_reason, "Kevin", expires, target_weight=0.05
        )

    oid = p.save_override("AAA", "exclude", ok_reason, "Kevin", expires)
    assert oid == 1
    df = p.load_overrides()
    assert len(df) == 1 and df.loc[0, "status"] == "active"

    oid2 = p.save_override(
        "BBB", "weight", ok_reason, "Kevin", expires, target_weight=0.04
    )
    assert oid2 == 2

    p.close_override(oid, "Kevin", "erledigt")
    df2 = p.load_overrides()
    assert df2.loc[df2["id"] == oid, "status"].iloc[0] == "closed"


def test_override_expiry(tmp_path, monkeypatch):
    """Abgelaufene Overrides werden nicht angewendet, Diagnose vorhanden
    (Test 22)."""
    p = _fresh_db(tmp_path, monkeypatch)
    reason = "Temporärer Ausschluss wegen laufender Untersuchung."
    p.save_override("AAA", "exclude", reason, "Kevin", date.today() + timedelta(days=5))

    snap = date.today() + timedelta(days=30)
    expired = p.expire_overrides(snap)
    assert [e["uid"] for e in expired] == ["AAA"]
    df = p.load_overrides()
    assert df.loc[0, "status"] == "expired"

    # Nicht mehr angewendet …
    assert active_overrides(df, snap).empty
    # … aber als Diagnose gelistet.
    diags = override_expiry_diagnostics(df, snap)
    assert len(diags) == 1
    assert diags[0].severity == SEV_WARNING
    assert "abgelaufen" in diags[0].message


def test_override_weights_separate():
    """weight_model ≠ weight_effective genau bei aktiven Overrides
    (Test 23)."""
    weights = pd.Series(0.2, index=[f"T{i}" for i in range(5)])
    overrides = pd.DataFrame(
        {
            "id": [1],
            "uid": ["T0"],
            "direction": ["weight"],
            "target_weight": [0.10],
            "status": ["active"],
            "expires_at": [(date.today() + timedelta(days=30)).isoformat()],
        }
    )
    diags: list = []
    model, effective, ids = apply_overrides(weights, overrides, Settings(), diags)
    pd.testing.assert_series_equal(model, weights)
    assert effective.loc["T0"] == pytest.approx(0.10)
    assert effective.drop("T0").sum() == pytest.approx(0.90)
    assert effective.sum() == pytest.approx(1.0)
    assert ids == {"T0": 1}
    assert not model.equals(effective)

    # Ohne aktive Overrides sind beide Gewichte identisch.
    model2, effective2, ids2 = apply_overrides(weights, None, Settings(), [])
    pd.testing.assert_series_equal(model2, effective2)
    assert ids2 == {}


def test_universe_benchmark_weights():
    """Benchmark-Quelle "universe": Sektor-/Regionsgewichte sind die
    marktkapitalisierungsgewichteten Anteile des gesamten Daten-Imports."""
    from app.core.portfolio_construction import universe_benchmark_weights

    universe = pd.DataFrame(
        {
            "uid": ["A", "B", "C", "D"],
            "sector": ["Tech", "Tech", "Health", "Health"],
            "region": ["US", "Europe", "US", "Europe"],
            "market_cap": [600.0, 200.0, 100.0, 100.0],
        }
    )
    bm = universe_benchmark_weights(universe)
    assert bm.sector is not None and bm.region is not None
    assert bm.sector["Tech"] == pytest.approx(0.8)
    assert bm.sector["Health"] == pytest.approx(0.2)
    assert bm.region["US"] == pytest.approx(0.7)
    assert bm.region["Europe"] == pytest.approx(0.3)
    assert any(d.code == "benchmark_universe" for d in bm.diagnostics)

    # Titel ohne Marktkapitalisierung → Gewicht 0 + Info; ohne jede
    # Marktkapitalisierung → Gleichgewichtung mit Warnung.
    uni_partial = universe.assign(market_cap=[600.0, np.nan, 100.0, 100.0])
    bm_partial = universe_benchmark_weights(uni_partial)
    assert bm_partial.sector["Tech"] == pytest.approx(600 / 800)
    assert any(
        d.code == "benchmark_universe_mcap_missing" for d in bm_partial.diagnostics
    )
    uni_none = universe.assign(market_cap=np.nan)
    bm_none = universe_benchmark_weights(uni_none)
    assert bm_none.sector["Tech"] == pytest.approx(0.5)
    assert any(
        d.code == "benchmark_universe_equal_weight" for d in bm_none.diagnostics
    )

    # load_benchmark_weights nutzt bei Quelle "universe" das Universum und
    # keinen Staleness-Check; ohne Universum → statischer Fallback + Warnung.
    s = _settings(pc_benchmark_source="universe")
    bm_loaded = load_benchmark_weights(s, universe=universe)
    assert bm_loaded.sector == bm.sector
    bm_fallback = load_benchmark_weights(s)
    assert any(
        d.code == "benchmark_universe_unavailable"
        for d in bm_fallback.diagnostics
    )


def test_universe_benchmark_in_selection():
    """Selektion mit Universums-Benchmark: das Band gilt gegen die
    Universums-Anteile (Ende-zu-Ende über build_model_portfolio)."""
    from datetime import date as _date

    from app.core.portfolio_construction import build_model_portfolio

    universe = pd.DataFrame(
        [
            {"uid": "T1", "sector": "Tech", "region": "US",
             "zone_v2": "KANDIDAT", "composite_z": 2.0, "composite_pct": 0.95,
             "volatility_1y": 0.2, "market_cap": 500.0},
            {"uid": "T2", "sector": "Tech", "region": "US",
             "zone_v2": "KANDIDAT", "composite_z": 1.5, "composite_pct": 0.9,
             "volatility_1y": 0.2, "market_cap": 500.0},
            {"uid": "H1", "sector": "Health", "region": "Europe",
             "zone_v2": "KANDIDAT", "composite_z": 1.0, "composite_pct": 0.85,
             "volatility_1y": 0.2, "market_cap": 500.0},
            {"uid": "H2", "sector": "Health", "region": "Europe",
             "zone_v2": "KANDIDAT", "composite_z": 0.8, "composite_pct": 0.82,
             "volatility_1y": 0.2, "market_cap": 500.0},
        ]
    )
    s = _settings(pc_benchmark_source="universe", pc_sector_band=0.55,
                  pc_region_band=0.55)
    result = build_model_portfolio(
        universe, s, {}, mode="full", snapshot_date=_date(2026, 9, 4)
    )
    # Benchmark je Sektor 50 %/50 % — mit Band ±55 pp passen alle 4 Titel
    # (der Aufbau läuft über Zwischenstände mit hohen Einzelgewichten).
    assert set(result["portfolio"]["uid"]) == {"T1", "T2", "H1", "H2"}
    assert any(
        d.code == "benchmark_universe" for d in result["diagnostics"]
    )

    # Enges Band ±30 pp: Der zweite Tech-Titel würde Tech beim Aufbau auf
    # 100 % heben (Benchmark 50 %) → übersprungen; Health-Titel passen.
    s2 = _settings(pc_benchmark_source="universe", pc_sector_band=0.30,
                   pc_region_band=0.90)
    result2 = build_model_portfolio(
        universe, s2, {}, mode="full", snapshot_date=_date(2026, 9, 4)
    )
    assert set(result2["portfolio"]["uid"]) == {"T1", "H1", "H2"}
