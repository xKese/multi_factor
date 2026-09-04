"""Tests für die Modellportfolio-Persistenz (Spec 13, Test 24)."""

from __future__ import annotations

import importlib
from datetime import date

import pandas as pd

from app.core import persistence
from app.core.config import Settings


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    importlib.reload(persistence)
    return persistence


def _portfolio(weight: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "uid": ["AAA", "BBB"],
            "composite_z": [1.2, 0.8],
            "composite_pct": [0.9, 0.8],
            "zone_v2": ["KANDIDAT", "KANDIDAT"],
            "weight_model": [weight, 1 - weight],
            "weight_effective": [weight, 1 - weight],
            "cte": [0.01, 0.02],
            "action": ["KAUF", "KAUF"],
            "reason": ["zone_KANDIDAT", "zone_KANDIDAT"],
            "rebalance_mode": ["full", "full"],
            "override_id": [None, None],
        }
    )


def _meta(p) -> dict:
    return {
        "rebalance_mode": "full",
        "n_titles": 2,
        "te_ex_ante": 0.05,
        "te_coverage": 0.9,
        "turnover_oneway": 0.1,
        "n_trades": 2,
        "n_deferred": 0,
        "settings_hash": p.settings_hash_v2(Settings()),
        "diagnostics": "[]",
    }


def test_model_portfolio_upsert(tmp_path, monkeypatch):
    """Gleiches Datum ersetzt, älteres Datum bleibt; settings_hash ändert
    sich bei Settings-Änderung (Test 24)."""
    p = _fresh_db(tmp_path, monkeypatch)

    old_date, new_date = date(2026, 3, 31), date(2026, 9, 30)
    p.save_model_portfolio(_portfolio(0.6), _meta(p), old_date)
    p.save_model_portfolio(_portfolio(0.6), _meta(p), new_date)

    # Re-Save mit gleichem Datum ersetzt nur diesen Snapshot.
    p.save_model_portfolio(_portfolio(0.7), _meta(p), new_date)

    newest = p.load_model_portfolio(new_date)
    oldest = p.load_model_portfolio(old_date)
    assert newest is not None and len(newest) == 2
    assert newest.set_index("uid").loc["AAA", "weight_model"] == 0.7
    assert oldest is not None
    assert oldest.set_index("uid").loc["AAA", "weight_model"] == 0.6

    # Ohne Datum: neuester Snapshot.
    latest = p.load_model_portfolio()
    assert latest["snapshot_date"].iloc[0] == new_date

    dates = p.list_model_portfolio_dates()
    assert dates == [new_date, old_date]

    meta = p.load_model_portfolio_meta(new_date)
    assert meta is not None and meta["rebalance_mode"] == "full"

    # settings_hash reagiert auf v2-/pc-Settings-Änderungen.
    s1, s2 = Settings(), Settings()
    s2.pc_target_n = 30
    assert p.settings_hash_v2(s1) != p.settings_hash_v2(s2)
    assert p.settings_hash_v2(s1) == p.settings_hash_v2(Settings())

    # Settings-Roundtrip: neue v2-/pc-Felder überleben Speichern/Laden.
    s3 = Settings()
    s3.pc_target_n = 33
    s3.v2_weight_value = 0.35
    s3.v2_weight_quality = 0.25
    s3.scoring_version = "v1"
    p.save_settings(s3)
    loaded = p.load_settings()
    assert loaded is not None
    assert loaded.pc_target_n == 33
    assert loaded.v2_weight_value == 0.35
    assert loaded.scoring_version == "v1"
