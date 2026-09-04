"""Tabellen-Helfer: v2-Spaltenformate und Zonen-/Klassen-/Action-Farben."""

from __future__ import annotations

import pandas as pd

from app.core.scoring_v2 import classify_v2
from app.pages.common import (
    SCORE_COLORS_V2,
    _column_def,
    render_table,
)


def test_column_def_composite_score():
    d = _column_def("composite_score")
    assert d["name"] == "Composite-Score (v2)"
    assert d["type"] == "numeric"


def test_column_def_percent_columns():
    for col in ("data_coverage_v2", "composite_pct", "weight_effective"):
        d = _column_def(col)
        assert d["type"] == "numeric", col
        # d3-Format "p" = Prozent-Darstellung von Dezimalanteilen.
        assert d["format"].to_plotly_json()["specifier"].endswith("p"), col


def test_column_def_delta_w_signed_percent():
    d = _column_def("delta_w")
    spec = d["format"].to_plotly_json()["specifier"]
    assert spec.endswith("p") and spec.startswith("+")


def test_column_def_z_and_cte():
    z = _column_def("composite_z")["format"].to_plotly_json()["specifier"]
    assert ".2f" in z
    cte = _column_def("cte")["format"].to_plotly_json()["specifier"]
    assert ".3f" in cte


def test_score_colors_v2_cover_classify_values():
    pct_samples = [0.95, 0.85, 0.7, 0.55, 0.4, 0.1]
    for pct in pct_samples:
        assert classify_v2(pct) in SCORE_COLORS_V2


def test_render_table_zone_and_action_rules():
    df = pd.DataFrame(
        {
            "ticker": ["AAA"],
            "zone_v2": ["KANDIDAT"],
            "classification_v2": ["B+"],
            "action": ["BUY"],
        }
    )
    table = render_table(df, id="t")
    rules = table.style_data_conditional
    queries = " | ".join(str(r.get("if", {}).get("filter_query")) for r in rules)
    for token in ("KANDIDAT", "HALTEN", "VERKAUFEN", "FILTER"):
        assert token in queries
    assert "classification_v2" in queries or any(
        r.get("if", {}).get("column_id") == "classification_v2" for r in rules
    )
    for action in ("BUY", "SELL", "DEFERRED"):
        assert action in queries
