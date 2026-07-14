"""Smoke-Tests für den Editorial-Factsheet-PDF-Export."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.factsheet_pdf import (
    _factor_weight_pct,
    build_context,
    compute_indicator_percentiles,
    compute_rank,
    generate_thesis,
    render_editorial_factsheet,
)
from app.core.indicators import INDICATOR_GROUPS
from app.core.scoring import compute_scores


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


def _scored_fixture() -> pd.DataFrame:
    df = load_koyfin_csv(FIXTURE.read_bytes())
    return compute_scores(df, Settings())


def test_compute_rank_returns_valid_position():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    rank = compute_rank(scored, ticker)
    # mind. einer der vier Werte sollte gesetzt sein
    assert any(rank[k] is not None for k in rank)
    if rank["sector_rank"] is not None:
        assert 1 <= rank["sector_rank"] <= rank["sector_total"]


def test_compute_indicator_percentiles_in_range():
    scored = _scored_fixture()
    for grp in INDICATOR_GROUPS:
        pct_map = compute_indicator_percentiles(scored, grp)
        for key, series in pct_map.items():
            valid = series.dropna()
            if not valid.empty:
                assert valid.min() >= 0 and valid.max() <= 100


def test_generate_thesis_returns_german_text():
    scored = _scored_fixture()
    row = scored.iloc[0]
    thesis = generate_thesis(row)
    assert isinstance(thesis, str)
    assert len(thesis) > 10


def test_build_context_minimal_keys():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    ctx = build_context(ticker, scored, Settings())
    for key in (
        "ticker",
        "name",
        "total_score",
        "classification",
        "thesis",
        "factors",
        "indicator_groups",
        "filter_badges",
        "returns",
        "peers",
        "generated_on",
    ):
        assert key in ctx, f"missing context key: {key}"
    assert len(ctx["factors"]) == 5
    assert len(ctx["indicator_groups"]) == 5


def test_factor_weight_pct_defaults_match_settings():
    """Mit den Default-Settings müssen die Faktor-Gewichte im PDF exakt den
    klassischen Anzeigewerten 25/27/15/18/15 entsprechen."""
    s = Settings()
    assert _factor_weight_pct(s, "Value") == 25
    assert _factor_weight_pct(s, "Quality") == 27
    assert _factor_weight_pct(s, "Growth") == 15
    assert _factor_weight_pct(s, "Momentum") == 18
    assert _factor_weight_pct(s, "Low Vol") == 15
    # Auch das Indikator-Gruppen-Label "Low Volatility" muss greifen
    assert _factor_weight_pct(s, "Low Volatility") == 15
    # Settings-Key-Form wird ebenfalls akzeptiert
    assert _factor_weight_pct(s, "value") == 25


def test_factor_weight_pct_reflects_user_changes():
    """Wenn der User in /einstellungen die Gewichte ändert, muss der
    PDF-Export die neuen Werte ziehen — nicht die hartcodierten Defaults."""
    s = Settings()
    s.factor_weights = {
        "value": 0.40,
        "quality": 0.20,
        "growth": 0.10,
        "momentum": 0.20,
        "lowvol": 0.10,
    }
    assert _factor_weight_pct(s, "Value") == 40
    assert _factor_weight_pct(s, "Quality") == 20
    assert _factor_weight_pct(s, "Low Vol") == 10


def test_factor_weight_pct_falls_back_when_missing():
    """Fehlt der Settings-Key (z.B. wegen Daten-Schema-Drift), fällt der
    Wert auf den Default zurück — keine Exception."""
    s = Settings()
    s.factor_weights = {"quality": 0.50}  # value/growth/momentum/lowvol fehlen
    assert _factor_weight_pct(s, "Quality") == 50
    assert _factor_weight_pct(s, "Value") == 25  # Fallback


def test_build_context_propagates_settings_weights_to_factors_and_groups():
    """Beide PDF-Sektionen (Faktor-Profil links, Indikator-Gruppen rechts)
    müssen denselben Gewicht-Wert pro Faktor anzeigen — sonst widersprechen
    sich die zwei Seiten des Factsheets."""
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    custom = Settings()
    custom.factor_weights = {
        "value": 0.33,
        "quality": 0.22,
        "growth": 0.11,
        "momentum": 0.22,
        "lowvol": 0.12,
    }

    ctx = build_context(ticker, scored, custom)

    factors_by_label = {f["label"]: f["weight"] for f in ctx["factors"]}
    groups_by_name = {g["name"]: g["weight"] for g in ctx["indicator_groups"]}

    assert factors_by_label["Value"] == 33
    assert factors_by_label["Quality"] == 22
    assert factors_by_label["Low Vol"] == 12
    # Indikator-Gruppen-Label "Low Volatility" und Faktor-Label "Low Vol"
    # spiegeln denselben Settings-Key (lowvol).
    assert groups_by_name["Value"] == 33
    assert groups_by_name["Quality"] == 22
    assert groups_by_name["Low Volatility"] == 12


def test_render_editorial_factsheet_emits_one_page_pdf():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    pdf_bytes = render_editorial_factsheet(ticker, scored, Settings())

    assert pdf_bytes.startswith(b"%PDF-")
    assert len(pdf_bytes) > 5000

    try:
        from pypdf import PdfReader
        from io import BytesIO

        reader = PdfReader(BytesIO(pdf_bytes))
        assert len(reader.pages) == 1, f"expected 1 page, got {len(reader.pages)}"
    except ImportError:  # pypdf optional in CI
        pass
