"""Labels-Regression: keine rohen Snake-Case-IDs für v2-Spalten in der UI."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores
from app.core.scoring_v2 import compute_scores_v2
from app.ui.labels import FACTOR_GROUP_LABELS, INDICATOR_LABELS, label_for

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"

# v2-Schlüssel, die explizit gepflegt sein müssen (WP1 des Design-Plans).
V2_KEYS = [
    "composite_score",
    "composite_z",
    "composite_pct",
    "classification_v2",
    "zone_v2",
    "data_coverage_v2",
    "z_value",
    "z_quality",
    "z_momentum",
    "z_investment",
    "cov_value",
    "cov_quality",
    "cov_momentum",
    "cov_investment",
    "filter_pass",
    "filter_reasons",
    "trend_warning",
    "gp_ta",
    "accruals",
    "asset_growth",
    "share_issuance",
    "mom_12_1_adj",
    "fcf_yield",
    "fcf_yield_source",
    "ev_ebit",
    "net_debt_ebitda",
    "adv_3m",
    "ipo_date",
    "weight_current",
    "weight_model",
    "weight_effective",
    "weight_target",
    "delta_w",
    "cte",
    "action",
    "reason",
    "uid",
    "override_id",
]

# Interne/technische Spalten, die nie als Tabellen-Header auftauchen.
_WHITELIST_PREFIXES = ("is_", "ebit_proxy", "fcf_yield_calc", "fcf_yield_v2")


def test_all_v2_keys_have_labels():
    missing = [k for k in V2_KEYS if k not in INDICATOR_LABELS]
    assert not missing, f"Fehlende Labels: {missing}"


def test_factor_group_investment():
    assert "investment" in FACTOR_GROUP_LABELS


def test_prefix_fallback_uses_base_label():
    assert label_for("neut_level_gp_ta") == "Neutralisierung Gross Profit / Assets"
    assert label_for("z_ev_ebitda") == "Z EV/EBITDA"
    assert label_for("cov_roic") == "Abdeckung ROIC"


def test_no_title_case_fallback_for_v2_columns():
    """Jede von compute_scores_v2 erzeugte Spalte hat ein echtes Label."""
    df = load_koyfin_csv(FIXTURE)
    settings = Settings()
    scored = compute_scores(df, settings)
    before = set(scored.columns)
    out, _ = compute_scores_v2(scored, settings)
    new_cols = [c for c in out.columns if c not in before]
    assert new_cols, "compute_scores_v2 hat keine neuen Spalten erzeugt?"
    from app.ui.labels import _PREFIX_LABELS

    unlabeled = []
    for col in new_cols:
        if col.startswith(_WHITELIST_PREFIXES):
            continue
        if col in INDICATOR_LABELS:
            continue
        if any(col.startswith(prefix) for prefix, _ in _PREFIX_LABELS):
            continue
        unlabeled.append(col)
    assert not unlabeled, f"Kein Label gepflegt für: {unlabeled}"
