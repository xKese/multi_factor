"""Orchestrierung eines Backtest-Laufs: Basisfall, Pflicht-Sensitivitäten,
Kennzahlen, Faktorregression, Report-Dict (Spec 9/10)."""

from __future__ import annotations

import logging
from datetime import datetime

import pandas as pd

from . import metrics as mt
from .config import BacktestConfig, SENSITIVITY_VARIANTS, VARIANT_BASE, all_variants, apply_variant
from .dataset import BacktestDataset
from .factor_regression import run_factor_regressions
from .report import IR_WARNING_THRESHOLD, regression_table, run_id_for, sensitivity_row
from .simulator import BacktestResult, run_backtest

log = logging.getLogger(__name__)


def evaluate(result: BacktestResult, rf: float = 0.0) -> tuple[dict, float]:
    nav = result.nav_daily["nav"]
    bm = result.nav_daily["benchmark_nav"]
    m = mt.summary(nav, bm, rf=rf)
    turnover = mt.turnover_pa(result.rebalances, result.meta.get("n_trading_days", len(nav)))
    return m, turnover


def run_full(
    config: BacktestConfig,
    dataset: BacktestDataset,
    include_sensitivities: bool = True,
    only_variant: str | None = None,
    progress=None,
) -> dict:
    """Führt Basisfall (+ Sensitivitäten) aus und liefert das Report-Dict
    für ``report.save_run``/``report.build_markdown``."""
    progress = progress or (lambda msg: None)
    run_at = datetime.now()
    variants: dict[str, BacktestConfig]
    if only_variant and only_variant != VARIANT_BASE:
        variants = {VARIANT_BASE: apply_variant(config, VARIANT_BASE), only_variant: apply_variant(config, only_variant)}
    elif include_sensitivities:
        variants = all_variants(config)
    else:
        variants = {VARIANT_BASE: apply_variant(config, VARIANT_BASE)}

    results: dict[str, BacktestResult] = {}
    rows: list[dict] = []
    metrics_by_variant: dict[str, dict] = {}
    for name, cfg in variants.items():
        progress(f"Variante {name}")
        res = run_backtest(cfg, dataset, progress=lambda m, n=name: progress(f"[{n}] {m}"))
        results[name] = res
        m, turnover = evaluate(res, rf=cfg.bt_rf)
        metrics_by_variant[name] = m
        rows.append(sensitivity_row(name, res, m, turnover))

    base = results[VARIANT_BASE]
    base_metrics = metrics_by_variant[VARIANT_BASE]
    nav = base.nav_daily["nav"]
    bm = base.nav_daily["benchmark_nav"]
    turnover = mt.turnover_pa(base.rebalances, base.meta.get("n_trading_days", len(nav)))
    costs = mt.costs_bp_pa(float(base.meta.get("total_costs_eur", 0.0)), nav)
    regression = run_factor_regressions(nav, bm, dataset.fx.series, dataset.factors)
    ir_warning = False
    if "S1_equal_factor_weights" in metrics_by_variant:
        ir_base = base_metrics["active"]["information_ratio"]
        ir_s1 = metrics_by_variant["S1_equal_factor_weights"]["active"]["information_ratio"]
        if pd.notna(ir_base) and pd.notna(ir_s1) and abs(ir_base - ir_s1) > IR_WARNING_THRESHOLD:
            ir_warning = True

    return {
        "run_id": run_id_for(config.name, run_at),
        "base": base,
        "results": results,
        "metrics": base_metrics,
        "metrics_by_variant": metrics_by_variant,
        "turnover_pa": turnover,
        "costs_bp_pa": costs,
        "diagnostics_summary": mt.diagnostics_summary(base.rebalances, base.holdings),
        "regression": regression,
        "regression_table": regression_table(regression),
        "sensitivity_table": pd.DataFrame(rows),
        "calendar_years": mt.calendar_year_table(nav, bm, base.rebalances, base.trades),
        "rolling_3y": mt.rolling_3y(nav, bm),
        "ir_warning": ir_warning,
    }


def expected_variants() -> list[str]:
    return [VARIANT_BASE, *SENSITIVITY_VARIANTS]
