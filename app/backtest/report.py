"""Markdown-Report und CSV-Exporte (Spec 10). Der Vorbehaltsblock (Spec 12)
steht wörtlich an erster Stelle jedes Reports. Zahlenformat: Dezimalkomma,
Prozent mit einer Nachkommastelle."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SENSITIVITY_VARIANTS, VARIANT_BASE
from .factor_regression import FACTORS, flag_loading

CAVEAT_BLOCK = """VORBEHALTE – DIESER BACKTEST IST EIN PLAUSIBILITÄTSTEST, KEIN TRACK RECORD

1. Universum: nur US-gelistete Aktien. Das produktive Modell arbeitet global.
2. Fundamentaldaten sind nachträglich korrigierte Werte (restated), keine
   Punkt-in-Zeit-Daten. Dieser Bias verbessert Value-, Quality- und Investment-
   Signale systematisch. Der Reporting-Lag von 90 Tagen mildert, beseitigt ihn nicht.
3. EPS-Revisionen sind nicht verfügbar. Momentum besteht im Basisfall nur aus
   risikoadjustiertem 12-1-Preismomentum.
4. Sektorzuordnung ist die heutige, nicht die historische.
5. Delistings werden zum letzten Kurs verkauft; Konkursverluste sind unterschätzt
   (siehe Sensitivität S7).
6. Benchmark-Sektorgewichte sind ein Proxy aus dem eigenen Universum.
7. Kosten sind pauschal; Marktimpact bei illiquiden Titeln ist nicht modelliert.
8. Faktorgewichte, Schwellen und Bandbreiten wurden vor diesem Backtest festgelegt
   und nicht daran angepasst. Jede spätere Anpassung an Backtest-Ergebnisse muss
   als solche dokumentiert werden.
9. Ergebnisse sind brutto vor Steuern und vor Verwaltungsgebühren.

Erwartete Live-Prämien liegen deutlich unter Backtest-Werten (McLean/Pontiff 2016:
Ø 26 % out-of-sample, 58 % post-publication)."""

IR_WARNING_THRESHOLD = 0.2
CSV_FILES = ("nav_daily.csv", "holdings_by_date.csv", "trades.csv", "diagnostics.csv",
             "sensitivities.csv", "factor_regression.csv")


# ── Formatierung ─────────────────────────────────────────────────────────


def fmt(value, digits: int = 2) -> str:
    if value is None:
        return "–"
    try:
        if isinstance(value, str):
            return value
        if pd.isna(value):
            return "–"
    except (TypeError, ValueError):
        return str(value)
    return f"{float(value):,.{digits}f}".replace(",", "X").replace(".", ",").replace("X", ".")


def fmt_pct(value, digits: int = 1) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "–"
    return fmt(float(value) * 100.0, digits) + " %"


def fmt_int(value) -> str:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return "–"
    return f"{int(round(float(value))):,}".replace(",", ".")


def _date(value) -> str:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return "–"
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    try:
        return pd.Timestamp(value).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return str(value)


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out.extend("| " + " | ".join(str(c) for c in r) + " |" for r in rows)
    return out


# ── Lauf-Verzeichnis (Persistenz eines Runs) ─────────────────────────────


def run_id_for(config_name: str, run_at: datetime | None = None) -> str:
    stamp = (run_at or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"backtest_{config_name}_{stamp}"


def save_run(run: dict, report_dir: str | Path) -> Path:
    """Schreibt CSVs + ``run.json`` nach ``<report_dir>/<run_id>/`` und den
    Markdown-Report daneben (``<report_dir>/<run_id>.md``)."""
    out = Path(report_dir) / run["run_id"]
    out.mkdir(parents=True, exist_ok=True)
    base = run["base"]
    base.nav_daily.to_csv(out / "nav_daily.csv")
    base.holdings.to_csv(out / "holdings_by_date.csv", index=False)
    base.trades.to_csv(out / "trades.csv", index=False)
    diag = base.rebalances.copy()
    diag.to_csv(out / "diagnostics.csv", index=False)
    base.model_diagnostics.to_csv(out / "model_diagnostics.csv", index=False)
    base.factor_exposures.to_csv(out / "factor_exposures.csv", index=False)
    base.sector_weights.to_csv(out / "sector_weights.csv", index=False)
    base.contributions.rename("contribution").to_csv(out / "contributions.csv")
    run["sensitivity_table"].to_csv(out / "sensitivities.csv", index=False)
    run["regression_table"].to_csv(out / "factor_regression.csv", index=False)
    run["calendar_years"].to_csv(out / "calendar_years.csv")
    run["rolling_3y"].to_csv(out / "rolling_3y.csv")
    chart = pd.DataFrame(
        {
            "nav": base.nav_daily["nav"],
            "benchmark_nav": base.nav_daily["benchmark_nav"],
            "drawdown": base.nav_daily["nav"] / base.nav_daily["nav"].cummax() - 1.0,
            "benchmark_drawdown": base.nav_daily["benchmark_nav"] / base.nav_daily["benchmark_nav"].cummax() - 1.0,
        }
    )
    chart = chart.join(run["rolling_3y"][["ir"]].rename(columns={"ir": "rolling_ir_3y"}), how="left")
    chart.to_csv(out / "chart_data.csv")
    payload = {
        "run_id": run["run_id"],
        "meta": base.meta,
        "config": base.config.to_dict(),
        "metrics": _json_safe(run["metrics"]),
        "diagnostics_summary": _json_safe(run["diagnostics_summary"]),
        "regression": _json_safe(run["regression"]),
        "ir_warning": run.get("ir_warning", False),
        "turnover_pa": run.get("turnover_pa"),
        "costs_bp_pa": run.get("costs_bp_pa"),
    }
    (out / "run.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str),
                                  encoding="utf-8")
    md = build_markdown(run)
    (Path(report_dir) / f"{run['run_id']}.md").write_text(md, encoding="utf-8")
    return Path(report_dir) / f"{run['run_id']}.md"


def _json_safe(obj):
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (np.floating, float)):
        return None if np.isnan(obj) else float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    return obj


def load_run(run_dir: str | Path) -> dict:
    """Lädt einen gespeicherten Lauf (für ``report --run``) als Report-Dict."""
    from .simulator import BacktestResult
    from .config import config_from_dict

    out = Path(run_dir)
    payload = json.loads((out / "run.json").read_text(encoding="utf-8"))
    nav = pd.read_csv(out / "nav_daily.csv", index_col="date", parse_dates=True)
    cfg = config_from_dict({k: v for k, v in payload["config"].items() if k != "settings_overrides"})
    cfg.settings_overrides = payload["config"].get("settings_overrides", {})
    base = BacktestResult(
        config=cfg,
        settings_hash=payload["meta"].get("settings_hash", ""),
        nav_daily=nav,
        holdings=pd.read_csv(out / "holdings_by_date.csv"),
        trades=pd.read_csv(out / "trades.csv"),
        rebalances=pd.read_csv(out / "diagnostics.csv"),
        factor_exposures=pd.read_csv(out / "factor_exposures.csv"),
        sector_weights=pd.read_csv(out / "sector_weights.csv"),
        contributions=pd.read_csv(out / "contributions.csv", index_col=0)["contribution"],
        model_diagnostics=pd.read_csv(out / "model_diagnostics.csv"),
        meta=payload["meta"],
    )
    return {
        "run_id": payload["run_id"],
        "base": base,
        "metrics": payload["metrics"],
        "diagnostics_summary": payload["diagnostics_summary"],
        "regression": payload["regression"],
        "sensitivity_table": pd.read_csv(out / "sensitivities.csv"),
        "regression_table": pd.read_csv(out / "factor_regression.csv"),
        "calendar_years": pd.read_csv(out / "calendar_years.csv", index_col="year"),
        "rolling_3y": pd.read_csv(out / "rolling_3y.csv", index_col=0, parse_dates=True),
        "ir_warning": payload.get("ir_warning", False),
        "turnover_pa": payload.get("turnover_pa"),
        "costs_bp_pa": payload.get("costs_bp_pa"),
    }


# ── Markdown ─────────────────────────────────────────────────────────────


def _section_metrics(run: dict) -> list[str]:
    m = run["metrics"]
    pf, bm, ac = m["portfolio"], m["benchmark"], m["active"]
    rows = [
        ["Gesamtrendite", fmt_pct(pf["total_return"]), fmt_pct(bm["total_return"]), fmt_pct(ac["total_return"])],
        ["Rendite p. a. (geometrisch)", fmt_pct(pf["ann_return"]), fmt_pct(bm["ann_return"]), fmt_pct(ac["ann_return"])],
        ["Volatilität p. a.", fmt_pct(pf["volatility"]), fmt_pct(bm["volatility"]), "–"],
        ["Sharpe (rf = " + fmt_pct(run["base"].config.bt_rf) + ")", fmt(pf["sharpe"]), fmt(bm["sharpe"]), "–"],
        ["Max. Drawdown", fmt_pct(pf["max_drawdown"]), fmt_pct(bm["max_drawdown"]), fmt_pct(ac.get("max_rel_drawdown"))],
        ["Datum Max. Drawdown", _date(pf["max_drawdown_date"]), _date(bm["max_drawdown_date"]), "–"],
        ["Erholungsdauer (Handelstage)", fmt_int(pf["recovery_days"]) if pf["recovery_days"] is not None else "nicht erholt",
         fmt_int(bm["recovery_days"]) if bm["recovery_days"] is not None else "nicht erholt", "–"],
        ["Calmar", fmt(pf["calmar"]), fmt(bm["calmar"]), "–"],
        ["Tracking Error ex post", "–", "–", fmt_pct(ac["tracking_error"])],
        ["Information Ratio", "–", "–", fmt(ac["information_ratio"])],
        ["Beta", "–", "–", fmt(ac["beta"])],
        ["Trefferquote (Kalenderjahre aktiv > 0)", "–", "–", fmt_pct(ac["hit_rate"])],
        ["Beste 12-Monats-Periode aktiv", "–", "–", f"{fmt_pct(ac['best_12m'])} ({_date(ac.get('best_12m_date'))})"],
        ["Schlechteste 12-Monats-Periode aktiv", "–", "–", f"{fmt_pct(ac['worst_12m'])} ({_date(ac.get('worst_12m_date'))})"],
        ["Einseitiger Turnover p. a.", fmt_pct(run.get("turnover_pa")), "–", "–"],
        ["Kosten p. a. (bp des NAV)", fmt(run.get("costs_bp_pa"), 1), "–", "–"],
    ]
    te_ex_ante = pd.to_numeric(run["base"].rebalances.get("te_ex_ante"), errors="coerce")
    if te_ex_ante is not None and te_ex_ante.notna().any():
        rows.append(["Ø Ex-ante-TE je Stichtag (zum Vergleich)", "–", "–", fmt_pct(te_ex_ante.mean())])
    return ["## 3. Kennzahlen", ""] + _table(["Kennzahl", "Portfolio", "Benchmark", "aktiv"], rows) + [""]


def _section_years(run: dict) -> list[str]:
    cy = run["calendar_years"]
    rows = [
        [str(y), fmt_pct(r["portfolio"]), fmt_pct(r["benchmark"]), fmt_pct(r["active"]),
         fmt_pct(r.get("turnover")), fmt(r.get("costs_bp"), 1)]
        for y, r in cy.iterrows()
    ]
    return ["## 4. Kalenderjahre", ""] + _table(
        ["Jahr", "Portfolio", "Benchmark", "aktiv", "Turnover", "Kosten (bp)"], rows
    ) + [""]


def _section_regression(run: dict) -> list[str]:
    lines = ["## 6. Faktor-Exposures (Fama-French 5 + Momentum, USD, Newey-West 5 Lags)", ""]
    reg = run.get("regression")
    if not reg:
        lines.append("Keine Faktordaten im Cache — Regression nicht gerechnet "
                     "(`python -m app.backtest fetch` lädt die Kenneth-French-Dateien).")
        return lines + [""]
    lines.append("Erwartet: positive Ladungen auf HML, RMW, CMA und Mom, SMB ≈ 0. "
                 "Ladungen mit falschem Vorzeichen oder |t| < 2 sind mit 🔴 markiert.")
    lines.append("")
    headers = ["Reihe", "Alpha p. a.", "t(α)", *FACTORS, "R²", "N"]
    rows = []
    for name, label in (("portfolio", "Portfolio"), ("benchmark", "Benchmark"), ("active", "aktiv")):
        r = reg.get(name) or {}
        cells = [label, fmt_pct(r.get("alpha_pa")), fmt(r.get("alpha_t"))]
        for f in FACTORS:
            b = (r.get("betas") or {}).get(f)
            t = (r.get("tstats") or {}).get(f)
            if b is None:
                cells.append("–")
                continue
            mark = " 🔴" if name == "portfolio" and flag_loading(f, b, t if t is not None else float("nan")) else ""
            cells.append(f"{fmt(b, 3)} (t {fmt(t, 1)}){mark}")
        cells += [fmt(r.get("r2"), 3), fmt_int(r.get("n"))]
        rows.append(cells)
    return lines + _table(headers, rows) + [""]


def _section_exposures(run: dict) -> list[str]:
    ex = run["base"].factor_exposures
    lines = ["## 7. Ø Faktor-Z-Scores Portfolio vs. Universum", ""]
    if ex is None or ex.empty:
        return lines + ["Keine Daten.", ""]
    mean = ex.groupby("factor")[["portfolio", "universe"]].mean()
    rows = [[f, fmt(r["portfolio"]), fmt(r["universe"]), fmt(r["portfolio"] - r["universe"])]
            for f, r in mean.iterrows()]
    lines += ["**Im Mittel über alle Stichtage:**", ""]
    lines += _table(["Faktor", "Portfolio Ø z", "Universum Ø z", "Differenz"], rows) + [""]
    piv = ex.pivot_table(index="date", columns="factor", values="portfolio")
    rows = [[_date(d), *[fmt(r.get(f)) for f in piv.columns]] for d, r in piv.iterrows()]
    lines += ["**Je Stichtag (Portfolio):**", ""]
    lines += _table(["Stichtag", *piv.columns], rows) + [""]
    return lines


def _section_sectors(run: dict) -> list[str]:
    sw = run["base"].sector_weights
    lines = ["## 8. Sektorexposure vs. Proxy-Benchmark (Top-500 des Universums)", ""]
    if sw is None or sw.empty:
        return lines + ["Keine Daten.", ""]
    sw = sw.copy()
    sw["active"] = sw["portfolio"] - sw["benchmark"]
    mean = sw.groupby("sector")[["portfolio", "benchmark", "active"]].mean()
    max_abs = sw.groupby("sector")["active"].apply(lambda s: float(s.abs().max()))
    rows = [[s, fmt_pct(r["portfolio"]), fmt_pct(r["benchmark"]), fmt_pct(r["active"]), fmt_pct(max_abs[s])]
            for s, r in mean.sort_values("portfolio", ascending=False).iterrows()]
    lines += _table(["Sektor", "Portfolio Ø", "Benchmark Ø", "aktiv Ø", "max. |aktiv|"], rows)
    worst = sw.loc[sw["active"].abs().idxmax()]
    lines += ["", f"Größte Abweichung an einem Stichtag: {worst['sector']} am {_date(worst['date'])} "
              f"({fmt_pct(worst['active'])}).", ""]
    return lines


def _section_diagnostics(run: dict) -> list[str]:
    d = run["diagnostics_summary"] or {}
    rows = [
        ["Ø Anzahl Titel", fmt(d.get("avg_positions"), 1)],
        ["Ø Haltedauer (Stichtage je Titel)", fmt(d.get("avg_holding_rebalances"), 1)],
        ["Anteil Stichtage mit Notfüllung", fmt_pct(d.get("fill_zone_share"))],
        ["Anteil verschobener Trades", fmt_pct(d.get("deferred_share"))],
        ["Anteil Stichtage TE-Restriktion nicht erfüllbar", fmt_pct(d.get("te_unmet_share"))],
        ["Anteil Stichtage TE nicht prüfbar", fmt_pct(d.get("te_skipped_share"))],
        ["Anteil Stichtage unter Mindestanzahl", fmt_pct(d.get("below_min_share"))],
        ["Fehlende Kurse (gehaltene Titel, Tage)", fmt_int(d.get("missing_prices_total"))],
        ["Delistings (verkauft)", fmt_int(d.get("delistings_total"))],
        ["Nicht ausführbare Käufe", fmt_int(d.get("buy_failed_total"))],
        ["Ø Anteil Sektor „Unknown“ im Universum", fmt_pct(d.get("unknown_sector_share"))],
        ["Ø Cash-Anteil nach Rebalancing", fmt_pct(d.get("avg_cash_share"))],
    ]
    lines = ["## 9. Diagnosestatistik", ""] + _table(["Kennzahl", "Wert"], rows) + [""]
    md = run["base"].model_diagnostics
    if md is not None and not md.empty:
        counts = md.groupby(["severity", "code"]).size().sort_values(ascending=False).head(15)
        lines += ["**Häufigste Modell-Diagnosen (Schweregrad · Code · Anzahl):**", ""]
        lines += _table(["Schweregrad", "Code", "Anzahl"], [[s, c, fmt_int(n)] for (s, c), n in counts.items()])
        lines.append("")
    return lines


def _section_sensitivities(run: dict) -> list[str]:
    st = run["sensitivity_table"]
    lines = ["## 10. Sensitivitäten", ""]
    if st is None or st.empty:
        return lines + ["Keine Sensitivitäten gerechnet (`--no-sensitivities`).", ""]
    rows = [
        [r["variant"], r.get("description", ""), fmt_pct(r["ann_return"]), fmt_pct(r["volatility"]),
         fmt_pct(r["tracking_error"]), fmt(r["information_ratio"]), fmt_pct(r["max_drawdown"]),
         fmt_pct(r["turnover_pa"])]
        for _, r in st.iterrows()
    ]
    lines += _table(["Variante", "Änderung", "Rendite p. a.", "Vola", "TE", "IR", "Max DD", "Turnover p. a."], rows)
    lines.append("")
    if run.get("ir_warning"):
        lines += ["> ⚠ **Warnhinweis:** Basisfall und S1 (gleiche Faktorgewichte) weichen im IR um mehr als "
                  f"{fmt(IR_WARNING_THRESHOLD, 1)} ab — die Faktorgewichte treiben das Ergebnis.", ""]
    lines += ["S8 belegt `eps_revisions_3m` mit risikoadjustiertem 6-1-Momentum (Sensitivität, nicht "
              "Basisfall); der Basisfall nutzt nur `mom_12_1_adj` (dokumentierte Degradation).", ""]
    return lines


def _section_contributions(run: dict) -> list[str]:
    c = run["base"].contributions.dropna()
    lines = ["## 11. Größte Beiträge zur aktiven Rendite (kumuliert, Gewichtung × (r − r_Benchmark))", ""]
    if c.empty:
        return lines + ["Keine Daten.", ""]
    top = c.sort_values(ascending=False).head(20)
    bottom = c.sort_values().head(20)
    lines += ["**Positiv:**", ""] + _table(["Titel", "Beitrag"], [[u, fmt_pct(v, 2)] for u, v in top.items()]) + [""]
    lines += ["**Negativ:**", ""] + _table(["Titel", "Beitrag"], [[u, fmt_pct(v, 2)] for u, v in bottom.items()]) + [""]
    return lines


def build_markdown(run: dict) -> str:
    base = run["base"]
    meta = base.meta
    d = run["diagnostics_summary"] or {}
    lines: list[str] = ["```", CAVEAT_BLOCK, "```", ""]
    lines += [f"# Backtest {meta.get('config_name', '')} — {run['run_id']}", ""]
    lines += ["## 2. Lauf-Metadaten", ""]
    lines += _table(
        ["Feld", "Wert"],
        [
            ["Config-Name", meta.get("config_name", "")],
            ["Config-Hash", f"`{meta.get('config_hash', '')}`"],
            ["Settings-Hash (v2/pc/filter)", f"`{meta.get('settings_hash', '')}`"],
            ["Git-Commit Modellcode", f"`{meta.get('git_commit', '')}`"],
            ["Cache-Stand", meta.get("cache_asof", "") or "–"],
            ["Zeitraum", f"{_date(meta.get('start'))} – {_date(meta.get('end'))}"],
            ["Handelstage", fmt_int(meta.get("n_trading_days"))],
            ["Stichtage", fmt_int(meta.get("n_rebalances"))],
            ["Universumsgröße je Stichtag (Min / Median / Max)",
             f"{fmt_int(d.get('universe_min'))} / {fmt(d.get('universe_median'), 0)} / {fmt_int(d.get('universe_max'))}"],
            ["Startkapital", fmt(base.config.bt_initial_capital, 0) + " EUR"],
            ["Kosten je Seite", f"{fmt(base.config.bt_commission_bps, 0)} bp Kommission + "
                                f"{fmt(base.config.bt_slippage_bps, 0)} bp Slippage"],
            ["Reporting-Lag", f"{base.config.bt_reporting_lag_days} Tage"],
            ["Benchmark", f"{base.config.bt_benchmark_ticker} (EUR); Sektorgewichte: {base.config.bt_benchmark_sector_source}"],
            ["Laufzeit", f"{fmt(meta.get('elapsed_s'), 1)} s"],
            ["Erstellt", meta.get("run_at", "")],
        ],
    )
    lines.append("")
    lines += _section_metrics(run)
    lines += _section_years(run)
    lines += ["## 5. Chart-Daten", "",
              "Als CSV im Lauf-Verzeichnis: `chart_data.csv` (kumulierte NAV-Reihen Portfolio/Benchmark, "
              "Drawdowns, rollierende 3-Jahres-IR), `rolling_3y.csv` (aktive Rendite, TE, IR, Beta), "
              "`nav_daily.csv`.", ""]
    lines += _section_regression(run)
    lines += _section_exposures(run)
    lines += _section_sectors(run)
    lines += _section_diagnostics(run)
    lines += _section_sensitivities(run)
    lines += _section_contributions(run)
    return "\n".join(lines)


def sensitivity_row(name: str, result, metrics: dict, turnover: float) -> dict:
    desc = {
        VARIANT_BASE: "Basisfall",
        "S1_equal_factor_weights": "Faktorgewichte 0,25 / 0,25 / 0,25 / 0,25",
        "S2_no_buffer": "pc_exit_pct = pc_entry_pct (scharfe Top-35)",
        "S3_equal_weight": "Gewichtung 1/N (Cap/Floor bleiben)",
        "S4_no_te_constraint": "TE-Schritt übersprungen",
        "S5_quarterly_full": "jedes Quartal full",
        "S6_costs_x2": "Kosten 20 + 10 bp",
        "S7_delisting_haircut": "−30 % auf Delisting-Erlöse",
        "S8_momentum_proxy": "eps_revisions_3m := risikoadjustiertes 6-1-Momentum",
        "S9_lag_120": "Reporting-Lag 120 statt 90 Tage",
        "S10_top500": "Universum 500 statt 1.000 Titel",
    }
    m = metrics
    return {
        "variant": name,
        "description": desc.get(name, ""),
        "ann_return": m["portfolio"]["ann_return"],
        "volatility": m["portfolio"]["volatility"],
        "tracking_error": m["active"]["tracking_error"],
        "information_ratio": m["active"]["information_ratio"],
        "max_drawdown": m["portfolio"]["max_drawdown"],
        "turnover_pa": turnover,
        "nav_hash": result.nav_hash(),
    }


def regression_table(reg: dict | None) -> pd.DataFrame:
    rows = []
    for series, r in (reg or {}).items():
        for f in FACTORS:
            rows.append({"series": series, "factor": f, "beta": (r.get("betas") or {}).get(f),
                         "tstat": (r.get("tstats") or {}).get(f), "alpha_pa": r.get("alpha_pa"),
                         "alpha_t": r.get("alpha_t"), "r2": r.get("r2"), "n": r.get("n")})
    return pd.DataFrame(rows, columns=["series", "factor", "beta", "tstat", "alpha_pa", "alpha_t", "r2", "n"])
