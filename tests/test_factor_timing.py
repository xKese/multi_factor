"""Tests für die Factor-Timing-Seite: Strategische Gewichte werden aus den
Einstellungen abgeleitet, Eingabewerte werden persistiert.
"""

from __future__ import annotations

import pytest

import dash
import dash_bootstrap_components as dbc


@pytest.fixture(scope="module")
def factor_timing_module():
    """Initialisiert eine Dash-App, damit der Import von
    ``app.pages.factor_timing`` (mit ``register_page``-Aufruf) nicht raised.
    """
    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import factor_timing  # type: ignore[import-untyped]

    return factor_timing


def test_strategic_weights_mapped_from_settings(factor_timing_module, monkeypatch):
    """Die strategische Allokation muss die Settings-Faktor-Gewichte spiegeln
    (mit Mapping ``lowvol`` → ``Low Volatility``)."""
    from app.core.state import STATE

    monkeypatch.setattr(
        STATE.settings,
        "factor_weights",
        {"value": 0.30, "quality": 0.25, "growth": 0.20, "momentum": 0.15, "lowvol": 0.10},
        raising=False,
    )

    out = factor_timing_module._strategic_weights()

    assert set(out.keys()) == {"Value", "Quality", "Growth", "Momentum", "Low Volatility"}
    assert out["Value"] == pytest.approx(0.30, abs=1e-6)
    assert out["Quality"] == pytest.approx(0.25, abs=1e-6)
    assert out["Low Volatility"] == pytest.approx(0.10, abs=1e-6)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)


def test_strategic_weights_renormalize_when_sum_off(factor_timing_module, monkeypatch):
    """Wenn die Settings-Gewichte nicht auf 1 summieren, normalisiert das
    Factor-Timing-System sie — sonst zeigt das Bar-Chart falsche Prozente."""
    from app.core.state import STATE

    # Summe = 2.0 — soll nach Normalisierung wieder 1 ergeben
    monkeypatch.setattr(
        STATE.settings,
        "factor_weights",
        {"value": 0.40, "quality": 0.40, "growth": 0.40, "momentum": 0.40, "lowvol": 0.40},
        raising=False,
    )

    out = factor_timing_module._strategic_weights()
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)
    for v in out.values():
        assert v == pytest.approx(0.2, abs=1e-6)


def test_strategic_weights_fallback_on_missing_settings(factor_timing_module, monkeypatch):
    """Fehlen Settings-Faktor-Gewichte komplett, greift der hartcodierte
    Fallback."""
    from app.core.state import STATE

    monkeypatch.setattr(STATE.settings, "factor_weights", {}, raising=False)
    out = factor_timing_module._strategic_weights()
    # Fallback summiert exakt auf 1.0 (0.2375 × 4 + 0.05)
    assert sum(out.values()) == pytest.approx(1.0, abs=1e-6)
    assert out["Low Volatility"] == pytest.approx(0.05, abs=1e-6)


def test_resolve_input_values_uses_defaults_without_db(factor_timing_module, monkeypatch):
    """Ohne persistierte Werte (load liefert ``None``) fällt jedes Feld auf
    seinen hartcodierten Default zurück."""
    monkeypatch.setattr(
        factor_timing_module, "load_factor_timing_inputs", lambda: None
    )
    vals = factor_timing_module._resolve_input_values()
    assert vals == factor_timing_module._DEFAULTS
    # Spot-Check: alle Input-IDs sind abgedeckt
    assert "ft-pmi" in vals
    assert "ft-mom-lowvol" in vals


def test_resolve_input_values_overrides_with_stored(factor_timing_module, monkeypatch):
    """Persistierte Werte überschreiben Defaults — pro Feld-Mapping
    (snake_case-DB-Key → Input-ID)."""
    monkeypatch.setattr(
        factor_timing_module,
        "load_factor_timing_inputs",
        lambda: {"pmi": 52.5, "mom_value": 8.3, "cpi": None},
    )
    vals = factor_timing_module._resolve_input_values()
    assert vals["ft-pmi"] == 52.5
    assert vals["ft-mom-value"] == 8.3
    # None aus DB → bleibt Default (kein Override mit None)
    assert vals["ft-cpi"] == factor_timing_module._DEFAULTS["ft-cpi"]
    # Nicht persistierte Felder bleiben Default
    assert vals["ft-vix"] == factor_timing_module._DEFAULTS["ft-vix"]


def test_input_id_field_mapping_bijective(factor_timing_module):
    """Schutz vor Tippfehlern: jede Input-ID hat genau einen DB-Feldnamen und
    umgekehrt — sonst gehen Persist-Roundtrips kaputt."""
    mod = factor_timing_module
    assert set(mod._FIELD_TO_INPUT_ID.values()) == set(mod._INPUT_ID_TO_FIELD.keys())
    assert set(mod._FIELD_TO_INPUT_ID.keys()) == set(mod._INPUT_ID_TO_FIELD.values())
    # Reihenfolge im Save-Callback muss alle Input-IDs abdecken
    assert set(mod._INPUT_ORDER) == set(mod._DEFAULTS.keys())
    assert len(mod._INPUT_ORDER) == len(mod._DEFAULTS)


def test_persistence_field_list_matches_input_mapping(factor_timing_module):
    """Die Felder in ``persistence._FACTOR_TIMING_FIELDS`` müssen 1:1 zur
    Page-internen ``_FIELD_TO_INPUT_ID`` passen — sonst werden Felder beim
    Speichern verworfen."""
    from app.core.persistence import _FACTOR_TIMING_FIELDS

    assert set(_FACTOR_TIMING_FIELDS) == set(factor_timing_module._FIELD_TO_INPUT_ID.keys())
