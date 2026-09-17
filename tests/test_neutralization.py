"""Tests der sequenziellen Neutralisierung (Spec Sequenzielle Neutralisierung,
Abschnitt 10, Tests 1–11)."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from app.core.config import NEUT_SCHEMES, V2_INDICATORS, Settings
from app.core.data_loader import load_koyfin_csv
from app.core.gics import (
    SOURCE_GICS,
    SOURCE_SECTOR,
    assign_industry_group,
    industry_group_diagnostics,
    load_gics_map,
    normalize_industry_name,
)
from app.core.scoring import compute_scores
from app.core.scoring_v2 import (
    assign_neutralization_groups,
    compute_scores_v2,
    zscore_sequential,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures"
UNIVERSE_CSV = FIXTURES / "koyfin_universe_sample.csv"
LEGACY_REFERENCE = FIXTURES / "v2_legacy_reference.csv"


@pytest.fixture(scope="module")
def scored_universe() -> pd.DataFrame:
    """Reales Koyfin-Universum (1.296 Titel, Stand 2026-04-10), v1-gescort."""
    raw = load_koyfin_csv(str(UNIVERSE_CSV))
    return compute_scores(raw, Settings())


def _legacy_settings() -> Settings:
    s = Settings()
    s.v2_neut_scheme_default = "region_sector"
    s.v2_neut_scheme_by_indicator = {}
    return s


# ── Test 1: Mapping-Tabelle ─────────────────────────────────────────────────


def test_gics_map_complete():
    gmap = load_gics_map()
    assert set(gmap.columns) >= {"industry", "industry_group", "sector", "gics_version", "key"}
    # Alle Industrien haben Gruppe und Sektor; keine Duplikate (auch nicht
    # nach Normalisierung).
    assert (gmap["industry_group"] != "").all() and (gmap["sector"] != "").all()
    assert not gmap["industry"].duplicated().any()
    assert not gmap["key"].duplicated().any()
    # GICS 2023: 74 Industrien, 25 Industriegruppen, 11 Sektoren.
    current = gmap[gmap["gics_version"] == "2023"]
    assert len(current) == 74
    assert current["industry_group"].nunique() == 25
    assert current["sector"].nunique() == 11
    # Jede Gruppe gehört zu genau einem Sektor.
    assert gmap.groupby("industry_group")["sector"].nunique().max() == 1


# ── Test 2: Zuordnung beim Import ──────────────────────────────────────────


def test_assign_industry_group():
    df = pd.DataFrame(
        {
            "sector": [
                "Information Technology",
                "Consumer Discretionary",
                "Financials",
                "Real Estate",
                "Industrials",
            ],
            "industry": [
                "  Software  ",  # Trim
                "Hotels, Restaurants & Leisure",  # GICS-Schreibweise mit Komma und &
                "BANKS",  # Groß-/Kleinschreibung
                "Mortgage Real Estate Investment Trusts (REITs)",  # Sektorabweichung
                "Widgets & Gadgets",  # nicht im Mapping
            ],
        }
    )
    out = assign_industry_group(df)
    assert list(out["industry_group"]) == [
        "Software & Services",
        "Consumer Services",
        "Banks",
        "Financial Services",
        "Industrials",  # Fallback auf Sektor
    ]
    assert list(out["industry_group_source"]) == [
        SOURCE_GICS, SOURCE_GICS, SOURCE_GICS, SOURCE_GICS, SOURCE_SECTOR,
    ]
    # Eingabe bleibt unverändert; Aufruf ist idempotent.
    assert "industry_group" not in df.columns
    pd.testing.assert_frame_equal(assign_industry_group(out), out)

    diags = industry_group_diagnostics(out)
    warnings = [d for d in diags if d.code == "industry_not_in_gics_map"]
    assert len(warnings) == 1 and warnings[0].severity == "Warnung"
    assert "Widgets & Gadgets" in warnings[0].message and "1 Titel" in warnings[0].message
    infos = [d for d in diags if d.code == "industry_sector_deviation"]
    assert len(infos) == 1 and infos[0].severity == "Info"
    assert "Real Estate" in infos[0].message and "Financials" in infos[0].message
    # Koyfin-Sektor bleibt maßgeblich.
    assert out.loc[3, "sector"] == "Real Estate"

    # Normalisierung: & ↔ and, Trim, Groß-/Kleinschreibung, Satzzeichen.
    assert normalize_industry_name("Hotels, Restaurants & Leisure") == normalize_industry_name(
        "  hotels restaurants AND leisure "
    )


def test_loader_assigns_and_keeps_industry_group_column():
    """Der Loader ordnet die Gruppe zu; eine vorhandene Spalte „Industry
    Group" (Backtest-Roundtrip) wird kanonisiert übernommen."""
    raw = load_koyfin_csv(str(UNIVERSE_CSV))
    assert {"industry_group", "industry_group_source"} <= set(raw.columns)
    assert raw["industry_group"].notna().all()
    assert (raw["industry_group_source"] == SOURCE_GICS).all()
    assert raw["industry_group"].nunique() == 25

    from app.core.schema import KOYFIN_COLUMNS

    headers = [c.upper() for c in KOYFIN_COLUMNS] + ["Industry Group", "industry_group_source"]
    values = ["ACME", "Acme Corp", "Industrials", "Some AV Industry", "US"] + ["1"] * (
        len(KOYFIN_COLUMNS) - 8
    ) + ["375", "340", "2026-07-14", "Capital Goods", "av"]
    values[headers.index("LAST_PRICE")] = "380"
    csv = ",".join(headers) + "\n" + ",".join(values) + "\n"
    df = load_koyfin_csv(csv.encode("utf-8"))
    assert df["industry_group"].iloc[0] == "Capital Goods"
    assert df["industry_group_source"].iloc[0] == "av"
    assert df["sma_200"].iloc[0] == 340


# ── Test 3: Altschema bitidentisch ─────────────────────────────────────────


def test_legacy_scheme_identical(scored_universe):
    """Schema ``region_sector`` liefert bitidentische z_*, composite_z und
    neut_level_* wie vor der Änderung (Referenz aus dem alten Code)."""
    out, _ = compute_scores_v2(scored_universe, _legacy_settings())
    ref = pd.read_csv(LEGACY_REFERENCE, float_precision="round_trip")
    assert list(ref["uid"]) == list(out["uid"])
    for col in ref.columns:
        if col == "uid":
            continue
        assert col in out.columns, col
        if col.startswith("neut_level_"):
            assert (ref[col].fillna("") == out[col].fillna("")).all(), col
            assert set(out[col].dropna()) <= {"region_sector", "sector", "global"}
        else:
            assert np.array_equal(
                ref[col].to_numpy(), out[col].to_numpy(dtype=float), equal_nan=True
            ), col


# ── Tests 4/5: Richtung einmal, Cap ────────────────────────────────────────


def _two_level_frame(n: int = 60, seed: int = 3) -> tuple[pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    df = pd.DataFrame(
        {
            "industry_group": ["Software & Services"] * (n // 2) + ["Banks"] * (n - n // 2),
            "industry_group_source": SOURCE_GICS,
            "sector": ["Information Technology"] * (n // 2) + ["Financials"] * (n - n // 2),
            "region": ["Europe", "United States and Canada"] * (n // 2),
        }
    )
    values = pd.Series(rng.normal(size=n), index=df.index)
    values.iloc[0] = 50.0  # Ausreißer
    values.iloc[1] = -40.0
    return df, values


def test_sequential_direction_once():
    df, values = _two_level_frame()
    settings = Settings()
    g1, g2, labels = assign_neutralization_groups(
        df, "ev_ebitda", "industry_group_then_region", settings, valid_mask=values.notna()
    )
    assert g2 is not None and (labels == "industry_group>region").all()
    z_plus, _ = zscore_sequential(values, [g1, g2], 1.0, settings)
    z_minus, _ = zscore_sequential(values, [g1, g2], -1.0, settings)
    # Richtung −1 wirkt genau einmal: Vorzeichen nach Ebene 2 gespiegelt.
    np.testing.assert_allclose(z_minus.to_numpy(), -z_plus.to_numpy(), atol=1e-12)
    assert z_minus.corr(values, method="spearman") < 0
    assert z_plus.corr(values, method="spearman") > 0


def test_sequential_cap():
    df, values = _two_level_frame(n=200, seed=11)
    settings = Settings()
    g1, g2, _ = assign_neutralization_groups(
        df, "ev_ebitda", "industry_group_then_region", settings, valid_mask=values.notna()
    )
    z, degenerate = zscore_sequential(values, [g1, g2], -1.0, settings)
    assert not degenerate
    assert z.notna().sum() == len(values)
    assert z.abs().max() <= settings.v2_zscore_cap
    # Ebene 2 standardisiert erneut: innerhalb jeder Region Mittel ≈ 0.
    for _, idx in df.groupby("region").groups.items():
        assert abs(float(z.loc[idx].mean())) < 0.2


# ── Tests 6/7: Fallbacks je Ebene ──────────────────────────────────────────


def test_level1_fallback():
    """Gruppe < 20 → Sektor → Global; Beschriftung je Titel korrekt."""
    rows = []
    rows += [("Capital Goods", "Industrials", "Europe")] * 25  # Gruppe groß genug
    rows += [("Transportation", "Industrials", "Europe")] * 10  # Gruppe klein, Sektor 35
    rows += [("Media & Entertainment", "Communication Services", "Europe")] * 10  # Sektor klein
    rows += [("Industrials", "Industrials", "Europe")] * 3  # Fallback aus 1.4 → Sektorgruppe
    df = pd.DataFrame(rows, columns=["industry_group", "sector", "region"])
    df["industry_group_source"] = SOURCE_GICS
    df.loc[df.index[-3:], "industry_group_source"] = SOURCE_SECTOR
    valid = pd.Series(True, index=df.index)
    settings = Settings()

    g1, g2, labels = assign_neutralization_groups(
        df, "gp_ta", "industry_group_then_region", settings, valid_mask=valid
    )
    assert (labels.iloc[:25] == "industry_group>region").all()
    assert (labels.iloc[25:35] == "sector>region").all()
    assert (labels.iloc[35:45] == "global>region").all()
    assert (labels.iloc[45:] == "sector>region").all()
    assert (g1.iloc[:25] == "ig:Capital Goods").all()
    assert (g1.iloc[25:35] == "sec:Industrials").all()
    assert (g1.iloc[45:] == "sec:Industrials").all()  # keine Pseudo-Gruppe
    assert (g1.iloc[35:45] == "__global__").all()
    assert (g2 == "reg:Europe").all()

    # Gruppengröße zählt nur gültige Werte: ohne Werte in Capital Goods
    # fällt auch diese Gruppe zurück.
    valid2 = valid.copy()
    valid2.iloc[:10] = False
    _, _, labels2 = assign_neutralization_groups(
        df, "gp_ta", "industry_group_then_region", settings, valid_mask=valid2
    )
    assert (labels2.iloc[:25] == "sector>region").all()

    # sector_then_region: Sektor < 20 → direkt global.
    _, _, labels3 = assign_neutralization_groups(
        df, "accruals", "sector_then_region", settings, valid_mask=valid
    )
    assert (labels3.iloc[:35] == "sector>region").all()
    assert (labels3.iloc[35:45] == "global>region").all()
    assert (labels3.iloc[45:] == "sector>region").all()

    # region_only: Region < Mindestgröße → global.
    small = pd.DataFrame({"region": ["Africa / Middle East"] * 5 + ["Europe"] * 30})
    _, g2r, labels4 = assign_neutralization_groups(
        small, "mom_12_1_adj", "region_only", settings, valid_mask=pd.Series(True, index=small.index)
    )
    assert g2r is None
    assert (labels4.iloc[:5] == "global").all() and (labels4.iloc[5:] == "region").all()


def test_level2_skip():
    """Region < 30 → Ebene 2 entfällt, Beschriftung ohne ``>region``."""
    df = pd.DataFrame(
        {
            "industry_group": ["Capital Goods"] * 50,
            "industry_group_source": SOURCE_GICS,
            "sector": ["Industrials"] * 50,
            "region": ["Europe"] * 40 + ["Latin America and Caribbean"] * 10,
        }
    )
    settings = Settings()
    values = pd.Series(np.linspace(-1, 1, 50), index=df.index)
    g1, g2, labels = assign_neutralization_groups(
        df, "gp_ta", "industry_group_then_region", settings, valid_mask=values.notna()
    )
    assert (labels.iloc[:40] == "industry_group>region").all()
    assert (labels.iloc[40:] == "industry_group").all()
    assert g2.iloc[:40].eq("reg:Europe").all()
    assert g2.iloc[40:].isna().all()

    # Titel ohne Ebene 2 behalten den Wert der Ebene 1.
    z, _ = zscore_sequential(values, [g1, g2], 1.0, settings)
    z1, _ = zscore_sequential(values, [g1], 1.0, settings)
    np.testing.assert_allclose(z.iloc[40:].to_numpy(), z1.iloc[40:].to_numpy())
    assert not np.allclose(z.iloc[:40].to_numpy(), z1.iloc[:40].to_numpy())


# ── Test 8: Schema je Indikator ────────────────────────────────────────────


def test_scheme_by_indicator(scored_universe):
    settings = Settings()
    settings.v2_neut_scheme_default = "sector_then_region"
    settings.v2_neut_scheme_by_indicator = {
        "gp_ta": "global",
        "mom_12_1_adj": "region_only",
        "pb": "region_sector",
    }
    out, diags = compute_scores_v2(scored_universe, settings)
    assert set(out["neut_level_gp_ta"].dropna()) == {"global"}
    assert set(out["neut_level_mom_12_1_adj"].dropna()) <= {"region", "global"}
    assert set(out["neut_level_pb"].dropna()) <= {"region_sector", "sector", "global"}
    # Indikator ohne Eintrag erhält den Default.
    assert set(out["neut_level_ev_ebitda"].dropna()) <= {"sector>region", "sector", "global>region", "global"}
    assert out["neut_level_ev_ebitda"].dropna().str.startswith("sector").mean() > 0.9
    assert (out["neut_scheme_hash"] == settings.neut_scheme_hash()).all()

    # Default-Zuordnung (Spec 4) auf dem realen Universum: ev_ebitda wird
    # bei ≥ 85 % der Titel in der Industriegruppe standardisiert
    # (Abnahmekriterium 13), Fallback-Diagnosen sind Infos.
    out_default, diags_default = compute_scores_v2(scored_universe, Settings())
    level = out_default["neut_level_ev_ebitda"].dropna()
    assert level.str.startswith("industry_group").mean() >= 0.85
    assert set(out_default["neut_level_mom_12_1_adj"].dropna()) <= {"region", "global"}
    assert set(out_default["neut_level_accruals"].dropna()) <= {"sector>region", "sector"}
    assert any(d.code == "neut_fallback" for d in diags_default)
    assert any(d.code == "industry_group_sizes" for d in diags_default)
    assert not any(d.code == "neut_fallback_share" for d in diags_default)
    assert out_default["composite_z"].abs().max() <= 3.0

    # Ein zu kleines Universum löst die 25-%-Warnung aus.
    small = scored_universe.head(60)
    _, diags_small = compute_scores_v2(small, Settings())
    assert any(d.code == "neut_fallback_share" for d in diags_small)


# ── Test 9: Settings-Validierung ───────────────────────────────────────────


def test_settings_validation(scored_universe):
    ok = Settings()
    ok.validate_neut_schemes()
    assert set(ok.v2_neut_scheme_by_indicator) <= set(V2_INDICATORS)
    assert set(ok.v2_neut_scheme_by_indicator.values()) <= set(NEUT_SCHEMES)

    bad_scheme = Settings()
    bad_scheme.v2_neut_scheme_by_indicator = {"gp_ta": "sector_only"}
    with pytest.raises(ValueError, match="sector_only"):
        bad_scheme.validate_neut_schemes()

    bad_indicator = Settings()
    bad_indicator.v2_neut_scheme_by_indicator = {"total_score": "global"}
    with pytest.raises(ValueError, match="total_score"):
        bad_indicator.validate_neut_schemes()

    bad_default = Settings()
    bad_default.v2_neut_scheme_default = "industry"
    with pytest.raises(ValueError, match="v2_neut_scheme_default"):
        bad_default.validate_neut_schemes()

    # Import-Fehler: compute_scores_v2 validiert beim Aufruf.
    with pytest.raises(ValueError):
        compute_scores_v2(scored_universe.head(30), bad_scheme)


# ── Test 10: Schema-Hash ───────────────────────────────────────────────────


def test_scheme_hash():
    base = Settings()
    changed = Settings()
    changed.v2_neut_scheme_by_indicator["accruals"] = "region_only"
    assert base.neut_scheme_hash() != changed.neut_scheme_hash()

    # Sortierte Serialisierung: Einfügereihenfolge des Dicts ist egal.
    reordered = Settings()
    items = list(reordered.v2_neut_scheme_by_indicator.items())
    reordered.v2_neut_scheme_by_indicator = dict(reversed(items))
    assert reordered.neut_scheme_hash() == base.neut_scheme_hash()

    # Default wirkt nur auf Indikatoren ohne Eintrag.
    default_changed = Settings()
    default_changed.v2_neut_scheme_default = "global"
    assert default_changed.neut_scheme_hash() != base.neut_scheme_hash()

    # Der Settings-Hash (Modellportfolio) enthält die Zuordnung ebenfalls.
    from app.core.persistence import settings_hash_v2

    assert settings_hash_v2(base) != settings_hash_v2(changed)
    assert settings_hash_v2(base) == settings_hash_v2(reordered)


def test_settings_roundtrip_merges_scheme_dict():
    """Persistierte Zuordnung wird mit den Defaults gemergt (neue
    Indikatoren erhalten ihren Default)."""
    from app.core.persistence import _apply_settings_dict, _settings_to_dict

    s = Settings()
    s.v2_neut_scheme_by_indicator = {"gp_ta": "global"}
    payload = json.loads(json.dumps(_settings_to_dict(s)))
    loaded = Settings()
    _apply_settings_dict(loaded, payload)
    assert loaded.neut_scheme_for("gp_ta") == "global"
    assert loaded.neut_scheme_for("ev_ebitda") == "industry_group_then_region"
    assert loaded.v2_min_group_size_l1 == 20 and loaded.v2_min_group_size_l2 == 30


# ── Test 11: Compare-CLI ───────────────────────────────────────────────────


def test_compare_cli(scored_universe, tmp_path, monkeypatch):
    from app.tools.model_portfolio import (
        build_neutralization_report,
        compare_schemes,
        main,
    )
    from app.core.state import STATE

    result = compare_schemes(scored_universe, Settings(), "global", "global")
    assert result["rho_total"] == pytest.approx(1.0)
    assert result["movers"].empty
    assert len(result["top_intersection"]) == 35
    report = build_neutralization_report(result)
    for heading in (
        "## 1. Spearman-Rangkorrelation",
        "## 2. Titel mit Rangänderung",
        "## 3. Zusammensetzung der Top-35",
        "## 4. Gruppengrößenstatistik",
        "## 5. Titel in Fallback je Indikator",
    ):
        assert heading in report
    assert "1,000" in report and "Interpretationshinweis" in report

    # Echter Vergleich Alt- vs. Neuschema: Korrelation hoch, aber < 1.
    real = compare_schemes(scored_universe, Settings(), "region_sector", "industry_group_then_region")
    assert 0.75 < real["rho_total"] < 1.0
    assert real["hash_a"] != real["hash_b"]
    assert not real["movers"].empty

    # CLI-Lauf auf dem Koyfin-Export schreibt den Report.
    monkeypatch.setattr(STATE, "load_from_db", lambda: None)
    monkeypatch.setattr("app.core.persistence.load_overrides", lambda *a, **k: None)
    code = main(
        [
            "compare",
            "--scheme-a", "region_sector",
            "--scheme-b", "settings",
            "--csv", str(UNIVERSE_CSV),
            "--out", str(tmp_path),
        ]
    )
    assert code in (0, 1)
    path = tmp_path / "neutralisierung_vergleich_2026-04-10.md"
    assert path.exists()
    text = path.read_text(encoding="utf-8")
    assert "## 5. Titel in Fallback je Indikator" in text
    assert "Schema B: `settings`" in text
