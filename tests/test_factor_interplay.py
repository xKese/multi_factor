"""Tests der Regeltabelle für das Faktor-Zusammenspiel."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from app.core.analyst import factor_interplay as fi

# Je Regel ein positives und ein negatives Zeilen-Dict.
_CASES = {
    "value_trap": ({"z_value": 1.2, "z_momentum": -1.0}, {"z_value": 1.2, "z_momentum": 0.2}),
    "momentum_without_fundamentals": (
        {"z_momentum": 1.0, "z_quality": -0.9},
        {"z_momentum": 1.0, "z_quality": 0.1},
    ),
    "capex_expansion_on_weak_investment": (
        {"z_investment": -1.0, "z_asset_growth": -0.8, "asset_growth": 0.25},
        {"z_investment": -1.0, "z_asset_growth": 0.0},
    ),
    "revisions_vs_guidance_divergence": ({"z_eps_revisions_3m": -1.1}, {"z_eps_revisions_3m": -0.2}),
    "accruals_vs_cashflow_commentary": ({"z_accruals": -0.8, "accruals": 0.12}, {"z_accruals": 0.5}),
    "dilution_with_buyback_talk": ({"z_share_issuance": -1.5}, {"z_share_issuance": 0.3}),
    "leverage_vs_guidance_confidence": ({"z_debt_ebit": -1.0}, {"z_net_debt_ebitda": 0.4}),
    "quality_value_mismatch": ({"z_quality": 1.0, "z_value": -1.0}, {"z_quality": 1.0, "z_value": 0.0}),
    "trend_break_despite_revisions": (
        {"z_eps_revisions_3m": 1.0, "sma_signal": "⚠ DEATH CROSS"},
        {"z_eps_revisions_3m": 1.0, "sma_signal": "✓ GOLDEN CROSS"},
    ),
}


def test_every_rule_has_a_case():
    assert set(_CASES) == {r.code for r in fi.INTERPLAY_RULES}


@pytest.mark.parametrize("code", sorted(_CASES))
def test_rule_fires_only_on_positive_case(code):
    positive, negative = _CASES[code]
    assert code in {h.code for h in fi.detect_interplay(pd.Series(positive))}
    assert code not in {h.code for h in fi.detect_interplay(pd.Series(negative))}


def test_nan_is_safe():
    row = pd.Series({"z_value": float("nan"), "z_momentum": math.nan, "sma_signal": None})
    assert fi.detect_interplay(row) == []
    assert fi.detect_interplay({}) == []


def test_quality_drop_via_cursor():
    row = pd.Series({"z_momentum": 1.0, "z_quality": 0.3})
    assert fi.detect_interplay(row) == []
    hints = fi.detect_interplay(row, cursor={"last_z_quality": 0.9})
    assert [h.code for h in hints] == ["momentum_without_fundamentals"]
    assert hints[0].values["z_quality_change"] == pytest.approx(-0.6)


def test_hints_to_prompt_lists_codes_values_and_questions():
    hints = fi.detect_interplay(pd.Series({"z_value": 1.2, "z_momentum": -1.0}))
    text = fi.hints_to_prompt(hints)
    assert "[value_trap]" in text
    assert "z_value=1,20" in text
    assert "Prüffrage:" in text
    assert "Keine regelbasierten" in fi.hints_to_prompt([])


def test_leverage_fallback_order():
    m = fi.metrics_from_row(pd.Series({"z_debt_equity": -2.0, "z_debt_ebit": 0.5}))
    assert m["leverage_z"] == 0.5
