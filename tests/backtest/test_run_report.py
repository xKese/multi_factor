"""Tests 15–16: Sensitivitäten und Reproduzierbarkeit, Vorbehaltsblock zuerst."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from app.backtest.config import SENSITIVITY_VARIANTS, VARIANT_BASE, all_variants, apply_variant, load_config
from app.backtest.report import CAVEAT_BLOCK, CSV_FILES, build_markdown, load_run, save_run
from app.backtest.runner import run_full
from app.backtest.simulator import run_backtest

from .conftest import make_dataset, small_config


@pytest.fixture(scope="module")
def full_run():
    ds = make_dataset(n=24, seed=11)
    cfg = small_config(bt_start=date(2014, 3, 31), bt_end=date(2014, 12, 31))
    cfg.name = "unit"
    return cfg, ds, run_full(cfg, ds, include_sensitivities=True)


def test_sensitivities(full_run):
    """Alle 10 Varianten erzeugen Ergebnisse; Basisfall reproduzierbar (Test 15)."""
    cfg, ds, run = full_run
    table = run["sensitivity_table"]
    assert list(table["variant"]) == [VARIANT_BASE, *SENSITIVITY_VARIANTS]
    assert len(SENSITIVITY_VARIANTS) == 10
    assert table["ann_return"].notna().all() and table["nav_hash"].notna().all()

    # Reproduzierbarkeit: zweiter Basislauf mit gleichem Cache/Config → gleicher Hash.
    again = run_backtest(apply_variant(cfg, VARIANT_BASE), ds)
    assert again.nav_hash() == run["base"].nav_hash()
    assert again.nav_hash() == table.loc[table["variant"] == VARIANT_BASE, "nav_hash"].iloc[0]

    # Varianten wirken: S6 verdoppelt Kosten, S1 setzt gleiche Faktorgewichte,
    # S2 schärft die Pufferzone, S9 verschiebt den Reporting-Lag.
    results = run["results"]
    assert results["S6_costs_x2"].meta["total_costs_eur"] > results[VARIANT_BASE].meta["total_costs_eur"]
    s1 = apply_variant(cfg, "S1_equal_factor_weights").settings()
    assert s1.v2_factor_weights() == {"value": 0.25, "quality": 0.25, "momentum": 0.25, "investment": 0.25}
    s2 = apply_variant(cfg, "S2_no_buffer").settings()
    assert s2.pc_exit_pct == s2.pc_entry_pct
    assert apply_variant(cfg, "S9_lag_120").bt_reporting_lag_days == 120
    assert apply_variant(cfg, "S7_delisting_haircut").bt_delisting_haircut == pytest.approx(0.30)
    assert apply_variant(cfg, "S10_top500").bt_universe_top_n == 500
    assert apply_variant(cfg, "S5_quarterly_full").settings().pc_interim_months == []
    assert all(m == "full" for m in results["S5_quarterly_full"].rebalances["mode"])
    assert apply_variant(cfg, "S3_equal_weight").bt_equal_weight
    assert apply_variant(cfg, "S4_no_te_constraint").bt_skip_te_constraint
    assert results["S4_no_te_constraint"].rebalances["te_ex_ante"].isna().all()
    assert results[VARIANT_BASE].rebalances.loc[
        results[VARIANT_BASE].rebalances["mode"] == "full", "te_ex_ante"].notna().all()
    # S3: Gewichte an full-Stichtagen (bis auf Rundung) gleich; an
    # interim-Stichtagen bleiben die gedrifteten Gewichte (produktive Regel).
    h = results["S3_equal_weight"].holdings
    full_dates = set(results["S3_equal_weight"].rebalances.loc[
        results["S3_equal_weight"].rebalances["mode"] == "full", "date"])
    spread = h[h["date"].isin(full_dates)].groupby("date")["weight"].agg(lambda s: s.max() - s.min())
    assert spread.max() < 0.02
    assert len(all_variants(cfg)) == 11
    with pytest.raises(ValueError):
        apply_variant(cfg, "S99")


def test_report_caveats_first(full_run, tmp_path):
    """Vorbehaltsblock steht wörtlich am Anfang jedes Reports; alle
    Abschnitte und CSVs vorhanden (Test 16)."""
    cfg, ds, run = full_run
    md = build_markdown(run)
    assert md.startswith("```\n" + CAVEAT_BLOCK + "\n```")
    for n in range(2, 12):
        assert f"\n## {n}." in md, n
    for variant in SENSITIVITY_VARIANTS:
        assert variant in md
    assert "Config-Hash" in md and run["base"].meta["config_hash"] in md
    # Dezimalkomma im Zahlenformat.
    assert " %" in md and "0.0 %" not in md

    path = save_run(run, tmp_path)
    assert path.exists() and path.read_text(encoding="utf-8").startswith("```\n" + CAVEAT_BLOCK)
    run_dir = tmp_path / run["run_id"]
    for name in CSV_FILES:
        assert (run_dir / name).exists(), name
    nav = pd.read_csv(run_dir / "nav_daily.csv")
    assert {"nav", "benchmark_nav", "cash"} <= set(nav.columns)

    # report --run: aus dem gespeicherten Lauf erneut rendern.
    reloaded = load_run(run_dir)
    md2 = build_markdown(reloaded)
    assert md2.startswith("```\n" + CAVEAT_BLOCK)
    assert md2.count("\n## ") == md.count("\n## ")

    # YAML-Loader: Defaults, Overrides, unbekannte Keys.
    yaml_path = tmp_path / "cfg.yaml"
    yaml_path.write_text("bt_start: 2012-03-30\nsettings:\n  pc_target_n: 20\n", encoding="utf-8")
    loaded = load_config(yaml_path)
    assert loaded.name == "cfg" and loaded.bt_start == date(2012, 3, 30)
    assert loaded.settings().pc_target_n == 20 and loaded.bt_reporting_lag_days == 90
    yaml_path.write_text("bt_startx: 2012-03-30\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(yaml_path)
    assert load_config("configs/backtest_us_default.yaml").bt_universe_top_n == 1000
