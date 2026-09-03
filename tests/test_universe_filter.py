"""Tests für die Universumsfilter v2 (Spec 13, Tests 8–9)."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.universe_filter import apply_universe_filters


def _universe(**overrides) -> pd.DataFrame:
    data = {
        "uid": ["OK", "BAD", "FIN"],
        "sector": ["Industrials", "Industrials", "Financials"],
        "is_financial": [False, False, True],
        "is_real_estate": [False, False, False],
        "market_cap": [5000.0, 500.0, 5000.0],
        "piotroski": [7.0, 3.0, 4.0],
        "piotroski_max_criteria": [9.0, 9.0, 6.0],
        "altman_z": [3.0, 1.0, np.nan],
        "debt_equity": [1.0, 4.0, 8.0],
        "int_coverage": [10.0, 1.0, np.nan],
        "data_coverage_v2": [0.9, 0.4, 0.9],
        "composite_z": [1.0, np.nan, 0.5],
    }
    data.update(overrides)
    return pd.DataFrame(data)


def test_filters_all_reasons():
    """Mehrere Verstöße werden alle protokolliert (kein Abbruch beim ersten)."""
    out, _ = apply_universe_filters(_universe(), Settings())

    assert out.loc[0, "filter_pass"]
    assert out.loc[0, "filter_reasons"] == []

    reasons = out.loc[1, "filter_reasons"]
    assert not out.loc[1, "filter_pass"]
    # Alle Verstöße: Marktkapitalisierung, Piotroski, Altman, Abdeckung,
    # Extremverschuldung.
    for expected in ("market_cap", "piotroski", "altman", "coverage", "extreme_leverage"):
        assert expected in reasons, reasons

    # Financials: Altman übersprungen (trotz NaN), Piotroski-Schwelle
    # skaliert (4 von 6 ≥ 3,33), Extremverschuldung greift nicht.
    fin_reasons = out.loc[2, "filter_reasons"]
    assert out.loc[2, "filter_pass"], fin_reasons
    assert "altman_na" not in fin_reasons
    assert "extreme_leverage" not in fin_reasons

    # Fehlender Piotroski → eigener Grund "piotroski_na".
    out_na, _ = apply_universe_filters(
        _universe(piotroski=[np.nan, 3.0, 4.0]), Settings()
    )
    assert "piotroski_na" in out_na.loc[0, "filter_reasons"]


def test_filters_optional_columns():
    """Fehlende adv_3m/ipo_date → Filter übersprungen, Diagnose-Info da."""
    out, diags = apply_universe_filters(_universe(), Settings())
    codes = {d.code for d in diags}
    assert "filter_skipped_adv" in codes
    assert "filter_skipped_ipo" in codes
    assert all("liquidity" not in r for r in out["filter_reasons"])
    assert all("ipo" not in r for r in out["filter_reasons"])

    # Mit Spalten: Verstöße werden geflaggt.
    snap = date(2025, 6, 30)
    df = _universe(
        adv_3m=[10.0, 0.5, 10.0],
        ipo_date=["2010-01-01", "2025-05-01", "2010-01-01"],
    )
    out2, diags2 = apply_universe_filters(df, Settings(), snapshot_date=snap)
    assert "liquidity" in out2.loc[1, "filter_reasons"]
    assert "ipo" in out2.loc[1, "filter_reasons"]
    assert "liquidity" not in out2.loc[0, "filter_reasons"]
    codes2 = {d.code for d in diags2}
    assert "filter_skipped_adv" not in codes2
    assert "filter_skipped_ipo" not in codes2


def test_filter_override_exclude():
    """Aktiver Exclude-Override macht den Titel nicht eligible (Filter 8)."""
    overrides = pd.DataFrame(
        {
            "uid": ["OK", "FIN"],
            "direction": ["exclude", "exclude"],
            "status": ["active", "expired"],
            "expires_at": ["2099-01-01", "2020-01-01"],
        }
    )
    out, _ = apply_universe_filters(
        _universe(), Settings(), overrides=overrides, snapshot_date=date(2025, 6, 30)
    )
    assert "override_exclude" in out.loc[0, "filter_reasons"]
    # Abgelaufene/inaktive Overrides werden nicht mehr angewendet.
    assert "override_exclude" not in out.loc[2, "filter_reasons"]
