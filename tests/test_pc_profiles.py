"""Tests für Konstruktionsprofile: benannte Versionen der pc_*-Kriterien,
Zuordnung zu Portfolios und Auflösung der wirksamen Settings (Seite, CLI,
Einstellungen)."""

from __future__ import annotations

import importlib

import pandas as pd
import pytest

from app.core import persistence
from app.core.config import PC_PROFILE_FIELDS, Settings
from app.core.pc_profiles import (
    apply_profile,
    profile_differences,
    profile_payload,
    resolve_construction_settings,
)


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    importlib.reload(persistence)
    return persistence


def _portfolio(p, name: str) -> int:
    df = pd.DataFrame({"ticker": ["AAA", "BBB"], "name": ["Alpha", "Beta"]})
    portfolio_id, _n = p.save_ms_portfolio_named(df, name)
    return int(portfolio_id)


# ── Profilfelder und Anwendung ────────────────────────────────────────────


def test_profile_fields_are_exactly_the_pc_settings():
    assert PC_PROFILE_FIELDS
    assert all(f.startswith("pc_") for f in PC_PROFILE_FIELDS)
    # Universumsfilter und Benchmark-Stammdaten sind bewusst global.
    assert "filter_min_market_cap" not in PC_PROFILE_FIELDS
    assert "risk_benchmark_sector_weights_asof" not in PC_PROFILE_FIELDS
    assert "pc_te_target_low" in PC_PROFILE_FIELDS
    assert "pc_rebalance_months" in PC_PROFILE_FIELDS


def test_profile_payload_roundtrip_leaves_base_untouched():
    base = Settings()
    payload = profile_payload(base)
    assert set(payload) == set(PC_PROFILE_FIELDS)
    payload["pc_target_n"] = 20
    payload["pc_rebalance_months"].append(6)
    assert base.pc_target_n == 35
    assert base.pc_rebalance_months == [3, 9]


def test_apply_profile_coerces_types_and_ignores_unknown_keys():
    base = Settings()
    out = apply_profile(
        base,
        {
            "pc_target_n": 20.0,  # JSON/Number-Input → int
            "pc_weight_cap": 3,  # int → float
            "pc_rebalance_months": ["1", "7"],
            "pc_benchmark_source": "static",
            "not_a_field": 42,
            "filter_min_market_cap": 5000.0,  # kein Profilfeld → ignoriert
        },
    )
    assert out.pc_target_n == 20 and isinstance(out.pc_target_n, int)
    assert out.pc_weight_cap == 3.0 and isinstance(out.pc_weight_cap, float)
    assert out.pc_rebalance_months == [1, 7]
    assert out.pc_benchmark_source == "static"
    assert out.filter_min_market_cap == base.filter_min_market_cap
    # Basis unverändert, fehlende Keys behalten den globalen Wert.
    assert base.pc_target_n == 35
    assert out.pc_min_n == base.pc_min_n


def test_apply_profile_skips_unparseable_values():
    base = Settings()
    out = apply_profile(base, {"pc_target_n": "viele", "pc_te_max": 0.08})
    assert out.pc_target_n == base.pc_target_n
    assert out.pc_te_max == 0.08


def test_profile_differences_lists_only_changed_fields():
    base = Settings()
    diff = profile_differences(base, {"pc_target_n": 35, "pc_te_max": 0.08})
    assert diff == {"pc_te_max": (0.06, 0.08)}
    assert profile_differences(base, None) == {}


# ── Persistenz ────────────────────────────────────────────────────────────


def test_save_list_get_delete_profile(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    assert p.list_pc_profiles() == []

    pid = p.save_pc_profile("Konservativ", {"pc_target_n": 45, "pc_weight_cap": 0.03})
    assert pid == 1
    listed = p.list_pc_profiles()
    assert [x["name"] for x in listed] == ["Konservativ"]
    assert listed[0]["data"] == {"pc_target_n": 45, "pc_weight_cap": 0.03}
    assert p.get_pc_profile(pid)["name"] == "Konservativ"
    assert p.get_pc_profile(99) is None
    assert p.get_pc_profile(None) is None

    # Gleicher Name (case-insensitiv) → Update, keine zweite Zeile.
    same = p.save_pc_profile("konservativ", {"pc_target_n": 50})
    assert same == pid
    assert len(p.list_pc_profiles()) == 1
    assert p.get_pc_profile(pid)["data"] == {"pc_target_n": 50}
    assert p.get_pc_profile(pid)["name"] == "konservativ"

    other = p.save_pc_profile("Dynamisch", {"pc_target_n": 25})
    assert other == 2
    assert [x["name"] for x in p.list_pc_profiles()] == ["Dynamisch", "konservativ"]

    p.delete_pc_profile(pid)
    assert [x["id"] for x in p.list_pc_profiles()] == [other]


def test_save_profile_with_id_renames_and_rejects_name_collision(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    a = p.save_pc_profile("A", {"pc_target_n": 30})
    b = p.save_pc_profile("B", {"pc_target_n": 40})
    assert p.save_pc_profile("A neu", {"pc_target_n": 31}, profile_id=a) == a
    assert p.get_pc_profile(a)["name"] == "A neu"
    with pytest.raises(ValueError):
        p.save_pc_profile("A neu", {}, profile_id=b)
    with pytest.raises(ValueError):
        p.save_pc_profile("   ", {})


def test_assignments_follow_profile_lifecycle(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    depot_a = _portfolio(p, "Depot A")
    depot_b = _portfolio(p, "Depot B")
    kons = p.save_pc_profile("Konservativ", {"pc_target_n": 45})
    dyn = p.save_pc_profile("Dynamisch", {"pc_target_n": 25})

    assert p.list_pc_profile_assignments() == {}
    p.set_pc_profile_assignment(depot_a, kons)
    p.set_pc_profile_assignment(depot_b, dyn)
    assert p.list_pc_profile_assignments() == {depot_a: kons, depot_b: dyn}
    assert p.get_pc_profile_assignment(depot_a) == kons
    assert p.get_pc_profile_assignment(None) is None

    # Umhängen und Entfernen.
    p.set_pc_profile_assignment(depot_a, dyn)
    assert p.get_pc_profile_assignment(depot_a) == dyn
    p.set_pc_profile_assignment(depot_a, None)
    assert p.get_pc_profile_assignment(depot_a) is None

    # Unbekanntes Profil ist ein Fehler, keine stille Zuordnung.
    with pytest.raises(ValueError):
        p.set_pc_profile_assignment(depot_a, 999)

    # Löschen eines Profils räumt seine Zuordnungen weg → Standard.
    p.delete_pc_profile(dyn)
    assert p.get_pc_profile_assignment(depot_b) is None
    assert p.list_pc_profile_assignments() == {}


def test_model_portfolio_meta_keeps_profile(tmp_path, monkeypatch):
    from datetime import date

    p = _fresh_db(tmp_path, monkeypatch)
    meta = {
        "rebalance_mode": "full",
        "n_titles": 1,
        "te_ex_ante": 0.05,
        "te_coverage": 1.0,
        "turnover_oneway": 0.1,
        "n_trades": 1,
        "n_deferred": 0,
        "settings_hash": "abc",
        "diagnostics": "[]",
        "pc_profile_id": 7,
        "pc_profile_name": "Konservativ",
    }
    df = pd.DataFrame(
        [
            {
                "uid": "AAA",
                "composite_z": 1.0,
                "composite_pct": 0.9,
                "zone_v2": "KANDIDAT",
                "weight_model": 0.05,
                "weight_effective": 0.05,
                "cte": 0.01,
                "action": "BUY",
                "reason": "",
                "override_id": None,
            }
        ]
    )
    p.save_model_portfolio(df, meta, date(2026, 9, 1))
    stored = p.load_model_portfolio_meta(date(2026, 9, 1))
    assert stored["pc_profile_id"] == 7
    assert stored["pc_profile_name"] == "Konservativ"
    # Ohne Profil bleibt die Spalte NULL.
    meta.update({"pc_profile_id": None, "pc_profile_name": None})
    p.save_model_portfolio(df, meta, date(2026, 9, 2))
    assert p.load_model_portfolio_meta(date(2026, 9, 2))["pc_profile_id"] is None


# ── Auflösung der wirksamen Settings ──────────────────────────────────────


def test_resolve_uses_assigned_profile_else_base(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    base = Settings()
    depot = _portfolio(p, "Depot A")
    other = _portfolio(p, "Depot B")
    kons = p.save_pc_profile(
        "Konservativ",
        {"pc_target_n": 45, "pc_weight_cap": 0.03, "pc_te_target_high": 0.04},
    )
    p.set_pc_profile_assignment(depot, kons)

    settings, profile = resolve_construction_settings(base, depot)
    assert profile["id"] == kons and profile["name"] == "Konservativ"
    assert settings.pc_target_n == 45
    assert settings.pc_weight_cap == 0.03
    assert settings.pc_te_target_high == 0.04
    assert settings.pc_min_n == base.pc_min_n  # nicht im Profil → global
    assert base.pc_target_n == 35  # Basis bleibt unverändert

    settings_b, profile_b = resolve_construction_settings(base, other)
    assert profile_b is None and settings_b is base
    settings_n, profile_n = resolve_construction_settings(base, None)
    assert profile_n is None and settings_n is base


def test_resolve_changes_settings_hash(tmp_path, monkeypatch):
    """Ein Profil muss sich im Settings-Hash des Laufs niederschlagen."""
    p = _fresh_db(tmp_path, monkeypatch)
    base = Settings()
    depot = _portfolio(p, "Depot A")
    kons = p.save_pc_profile("Konservativ", {"pc_te_max": 0.045})
    p.set_pc_profile_assignment(depot, kons)
    settings, _ = resolve_construction_settings(base, depot)
    assert p.settings_hash_v2(settings) != p.settings_hash_v2(base)


def test_resolve_is_fail_open_on_db_error(monkeypatch):
    base = Settings()

    def boom(_pid):
        raise RuntimeError("db down")

    monkeypatch.setattr(persistence, "get_pc_profile_assignment", boom)
    settings, profile = resolve_construction_settings(base, 1)
    assert settings is base and profile is None


# ── Einstellungsseite ─────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def settings_page():
    import dash
    import dash_bootstrap_components as dbc

    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import einstellungen  # type: ignore[import-untyped]

    return einstellungen


def _pc_ids(page) -> list[dict]:
    return [
        {"type": "pc-set", "index": field}
        for field, _, _, _ in page.PC_SETTINGS_FIELDS
    ]


def test_settings_layout_has_profile_controls(settings_page, tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    p.save_pc_profile("Konservativ", {"pc_target_n": 45})
    options = settings_page._pc_profile_options()
    assert options[0]["value"] == settings_page.PC_PROFILE_DEFAULT
    assert [o["label"] for o in options[1:]] == ["Konservativ"]
    node = settings_page.layout()
    assert node is not None


def test_load_profile_fills_fields(settings_page, tmp_path, monkeypatch):
    from app.core.state import STATE

    p = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(STATE, "settings", Settings(), raising=False)
    pid = p.save_pc_profile(
        "Dynamisch",
        {"pc_target_n": 25, "pc_benchmark_source": "static",
         "pc_rebalance_months": [1, 7]},
    )
    ids = _pc_ids(settings_page)
    values, source, reb, interim, name = settings_page._load_pc_profile(str(pid), ids)
    by_field = dict(zip([i["index"] for i in ids], values, strict=True))
    assert by_field["pc_target_n"] == 25
    assert by_field["pc_min_n"] == 25  # global
    assert by_field["filter_min_market_cap"] == 1000.0
    assert source == "static" and reb == "1, 7" and interim == "6, 12"
    assert name == "Dynamisch"

    values, source, reb, _, name = settings_page._load_pc_profile("default", ids)
    by_field = dict(zip([i["index"] for i in ids], values, strict=True))
    assert by_field["pc_target_n"] == 35 and source == "universe" and name == ""


def test_manage_profiles_save_and_delete(settings_page, tmp_path, monkeypatch):
    from app.core.state import STATE

    p = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(STATE, "settings", Settings(), raising=False)
    monkeypatch.setattr(STATE, "refresh_portfolios", lambda: None)
    monkeypatch.setattr(STATE, "ms_portfolios", [], raising=False)
    ids = _pc_ids(settings_page)
    values = [getattr(Settings(), i["index"]) for i in ids]
    values[[i["index"] for i in ids].index("pc_target_n")] = 20

    monkeypatch.setattr(settings_page.ctx.__class__, "triggered_id", "pc-profile-save")
    options, value, status, body = settings_page._manage_pc_profiles(
        1, 0, "default", " Dynamisch ", values, ids, "universe", "3, 9", "6, 12"
    )
    stored = p.list_pc_profiles()
    assert [x["name"] for x in stored] == ["Dynamisch"]
    assert stored[0]["data"]["pc_target_n"] == 20
    assert "filter_min_market_cap" not in stored[0]["data"]
    assert set(stored[0]["data"]) == set(PC_PROFILE_FIELDS)
    assert value == str(stored[0]["id"])
    assert [o["label"] for o in options] == [settings_page.DEFAULT_PROFILE_LABEL, "Dynamisch"]

    # Leerer Name → nichts gespeichert.
    options, value, status, body = settings_page._manage_pc_profiles(
        2, 0, "default", "", values, ids, "universe", "", ""
    )
    assert len(p.list_pc_profiles()) == 1

    monkeypatch.setattr(settings_page.ctx.__class__, "triggered_id", "pc-profile-delete")
    options, value, status, body = settings_page._manage_pc_profiles(
        2, 1, str(stored[0]["id"]), "", values, ids, "universe", "", ""
    )
    assert p.list_pc_profiles() == []
    assert value == settings_page.PC_PROFILE_DEFAULT
    # Standard lässt sich nicht löschen.
    settings_page._manage_pc_profiles(2, 2, "default", "", values, ids, "universe", "", "")
    assert settings_page._pc_profile_options()[0]["value"] == "default"


def test_save_assignments(settings_page, tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    depot_a = _portfolio(p, "Depot A")
    depot_b = _portfolio(p, "Depot B")
    kons = p.save_pc_profile("Konservativ", {"pc_target_n": 45})
    settings_page._save_pc_assignments(
        1,
        [str(kons), "default"],
        [{"type": "pc-assign", "index": depot_a}, {"type": "pc-assign", "index": depot_b}],
    )
    assert p.list_pc_profile_assignments() == {depot_a: kons}


def test_global_save_writes_pc_fields_into_selected_profile(
    settings_page, tmp_path, monkeypatch
):
    """„Speichern" mit gewähltem Profil: pc_* landen im Profil, Filter und
    v2-Felder bleiben global."""
    from app.core.state import STATE

    p = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(STATE, "settings", Settings(), raising=False)
    monkeypatch.setattr(STATE, "recompute", lambda: None)
    pid = p.save_pc_profile("Konservativ", {"pc_target_n": 45})
    ids = _pc_ids(settings_page)
    fields = [i["index"] for i in ids]
    values = [getattr(Settings(), f) for f in fields]
    values[fields.index("pc_target_n")] = 50
    values[fields.index("filter_min_market_cap")] = 2000.0

    settings_page._save_v2(
        1, [], [], [], [], values, ids, "v2", "monitor", "static", "3, 9",
        "6, 12", "", "", None, [], [], str(pid),
    )
    assert STATE.settings.pc_target_n == 35  # global unverändert
    assert STATE.settings.pc_benchmark_source == "universe"
    assert STATE.settings.filter_min_market_cap == 2000.0  # Filter global
    stored = p.get_pc_profile(pid)["data"]
    assert stored["pc_target_n"] == 50
    assert stored["pc_benchmark_source"] == "static"
    assert stored["pc_rebalance_months"] == [3, 9]

    # Standard gewählt → globale Settings.
    settings_page._save_v2(
        2, [], [], [], [], values, ids, "v2", "monitor", "universe", "3, 9",
        "6, 12", "", "", None, [], [], "default",
    )
    assert STATE.settings.pc_target_n == 50


# ── Modellportfolio-Seite ─────────────────────────────────────────────────


@pytest.fixture(scope="module")
def mp_page():
    import dash
    import dash_bootstrap_components as dbc

    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import modellportfolio  # type: ignore[import-untyped]

    return modellportfolio


def test_source_options_show_assigned_profile(mp_page, tmp_path, monkeypatch):
    from app.core.state import STATE

    p = _fresh_db(tmp_path, monkeypatch)
    # Die Seite hält ``persistence`` als Modulreferenz — nach dem Reload
    # auf das frische Modul zeigen.
    monkeypatch.setattr(mp_page, "persistence", p)
    depot_a = _portfolio(p, "Depot A")
    depot_b = _portfolio(p, "Depot B")
    kons = p.save_pc_profile("Konservativ", {"pc_target_n": 45})
    p.set_pc_profile_assignment(depot_a, kons)
    monkeypatch.setattr(
        STATE,
        "ms_portfolios",
        [
            {"id": depot_a, "name": "Depot A", "n_positions": 2},
            {"id": depot_b, "name": "Depot B", "n_positions": 2},
        ],
        raising=False,
    )
    labels = [o["label"] for o in mp_page._source_options()]
    assert "Profil: Konservativ" in labels[0]
    assert "Profil:" not in labels[1]


def test_te_band_text_uses_profile(mp_page, tmp_path, monkeypatch):
    from app.core.state import STATE

    p = _fresh_db(tmp_path, monkeypatch)
    monkeypatch.setattr(mp_page, "persistence", p)
    monkeypatch.setattr(STATE, "settings", Settings(), raising=False)
    assert mp_page._te_band_text({}) == "4,5–5,5 %"
    assert mp_page._te_band_text({"te_target_low": 0.03, "te_target_high": 0.04}) == "3,0–4,0 %"
    pid = p.save_pc_profile("Konservativ", {"pc_te_target_low": 0.02, "pc_te_target_high": 0.03})
    # Historischer Lauf: nur die Profil-ID ist gespeichert.
    assert mp_page._te_band_text({"pc_profile_id": pid}) == "2,0–3,0 %"
