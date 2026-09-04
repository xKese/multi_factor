"""Deutsche Formatierung der v2-Kennzahlen (WP2 des Design-Plans)."""

from __future__ import annotations

from app.ui.formatters import (
    PERCENT_FIELDS,
    THREE_DEC_FIELDS,
    TWO_DEC_FIELDS,
    fmt_indicator,
    parse_de,
)


def test_v2_percent_fields_registered():
    for col in (
        "data_coverage_v2",
        "composite_pct",
        "cov_value",
        "cov_quality",
        "cov_momentum",
        "cov_investment",
        "weight_model",
        "weight_effective",
        "delta_w",
        "fcf_yield",
        "asset_growth",
    ):
        assert col in PERCENT_FIELDS, col


def test_v2_decimal_fields_registered():
    for col in ("composite_z", "z_value", "ev_ebit", "net_debt_ebitda"):
        assert col in TWO_DEC_FIELDS, col
    assert "cte" in THREE_DEC_FIELDS


def test_fmt_indicator_percent():
    assert fmt_indicator("data_coverage_v2", 0.755) == "75,5 %"
    assert fmt_indicator("composite_pct", 0.9) == "90,0 %"
    assert fmt_indicator("weight_model", 0.035) == "3,5 %"


def test_fmt_indicator_decimals():
    assert fmt_indicator("composite_z", 1.2345) == "1,23"
    assert fmt_indicator("cte", 0.0042) == "0,004"


def test_parse_de_roundtrip_unchanged():
    assert parse_de("1.234,56") == 1234.56
