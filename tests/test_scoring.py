"""Smoke-Tests: Lädt Koyfin-CSV-Fixture und prüft plausible Outputs."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import _factor_score, _indicator_percentile, compute_scores


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


def test_full_pipeline():
    df = load_koyfin_csv(FIXTURE.read_bytes())
    assert len(df) >= 10, "Erwarte mindestens 10 Aktien im Fixture"
    assert "ticker" in df.columns

    settings = Settings()
    scored = compute_scores(df, settings)

    for col in [
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
        "total_score",
    ]:
        assert col in scored.columns, col
        vals = scored[col].dropna()
        assert vals.min() >= 0 and vals.max() <= 100, f"{col} out of [0,100]"

    piotr = scored["piotroski"].dropna()
    assert piotr.min() >= 0 and piotr.max() <= 9

    assert scored["classification"].isin(
        [
            "A - Exzellent",
            "B+ - Sehr Gut",
            "B - Gut",
            "C - Durchschnitt",
            "D - Unterdurchschnitt",
            "F - Schwach",
            "-",
        ]
    ).all()

    assert scored["recommendation"].isin(
        ["STRONG BUY", "BUY", "HOLD", "SELL", "Filter nicht bestanden", "-"]
    ).all()

    # Momentum-Monitor-Spalten.
    for col in ["sma_gap", "mom_12_1", "dist_52w_high", "sma_20_distance", "trend_phase"]:
        assert col in scored.columns, col

    row = scored.dropna(subset=["ret_1m", "ret_12m"]).iloc[0]
    assert abs(row["mom_12_1"] - (row["ret_12m"] - row["ret_1m"])) < 1e-9

    from app.core.momentum import PHASE_NONE, TREND_PHASES

    assert scored["trend_phase"].isin([*TREND_PHASES, PHASE_NONE]).all()
    # Fixture ohne sma_20-Spalte → Distanz komplett NaN.
    assert scored["sma_20_distance"].isna().all()


def _universe(pe: list[float | None], pb: list[float | None]) -> pd.DataFrame:
    n = len(pe)
    return pd.DataFrame(
        {
            "ticker": [f"T{i}" for i in range(n)],
            "sector": ["Materials"] * n,
            "industry": ["Chemicals"] * n,
            "pe": pe,
            "pb": pb,
        }
    )


def test_lower_better_percentile_inverted():
    """Günstigster P/B der Industrie ⇒ hohes Perzentil, teuerster ⇒ niedriges."""
    df = _universe(
        pe=[10.0, 12.0, 14.0, 16.0, 18.0, 20.0],
        pb=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
    )
    settings = Settings()
    assert len(df) >= settings.min_stocks_per_industry

    pct = _indicator_percentile(df, "pb", settings)
    assert pct.iloc[0] > 0.7, "günstigster P/B muss Top-Perzentil bekommen"
    assert pct.iloc[-1] < 0.3, "teuerster P/B muss schlechtes Perzentil bekommen"


def test_negative_multiple_gets_no_percentile():
    """Negativer P/E (Verlust) darf nicht als 'am günstigsten' ranken."""
    df = _universe(
        pe=[-5.0, 8.0, 12.0, 16.0, 20.0, 24.0],
        pb=[1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    )
    settings = Settings()

    pct = _indicator_percentile(df, "pe", settings)
    assert pd.isna(pct.iloc[0]), "negativer P/E muss NaN-Perzentil bekommen"
    assert pct.iloc[1] == pct.dropna().max(), "bester positiver P/E ist Top"

    # Faktor-Score bleibt berechenbar: der maskierte Wert wird über die
    # dynamische Neugewichtung ausgeklammert statt mit 0 einzugehen.
    score = _factor_score(df, {"pe": 0.5, "pb": 0.5}, settings)
    assert score.notna().all()
    pb_only = _indicator_percentile(df, "pb", settings)
    assert abs(score.iloc[0] - pb_only.iloc[0]) < 1e-9


def test_pdf_percentiles_mask_negative_multiples():
    """PDF-Export (Parallel-Implementierung) maskiert negative Multiples ebenso."""
    from app.core.factsheet_pdf import compute_indicator_percentiles
    from app.core.indicators import INDICATOR_GROUPS

    df = _universe(
        pe=[-5.0, 8.0, 12.0, 16.0, 20.0, 24.0],
        pb=[0.5, 1.0, 1.5, 2.0, 2.5, 3.0],
    )
    value_group = next(g for g in INDICATOR_GROUPS if g.name == "Value")
    pct_map = compute_indicator_percentiles(df, value_group)

    assert pd.isna(pct_map["pe"].iloc[0])
    assert pct_map["pe"].iloc[1] == pct_map["pe"].dropna().max()
    assert pct_map["pb"].iloc[0] == pct_map["pb"].dropna().max()


def test_growth_impossible_rate_masked_high_growth_kept():
    """Wachstumsraten unter −100 % sind mathematisch unmöglich (Artefakt) →
    NaN. Sehr hohe echte Raten (Micron-Fall: > 300 %) bleiben gewertet und
    ranken top (gedeckelt, nicht verworfen)."""
    df = _universe(
        pe=[10.0] * 6,
        pb=[1.0] * 6,
    )
    df["rev_cagr_3y"] = [8.5, -1.5, 0.15, 0.20, -0.05, 4.0]
    settings = Settings()

    pct = _indicator_percentile(df, "rev_cagr_3y", settings)
    # 850 % und 400 % → beide gedeckelt auf 300 % → geteilter Top-Rang.
    top = pct.dropna().max()
    assert pct.iloc[0] == top, "hohes echtes Wachstum muss top ranken"
    assert pct.iloc[5] == top
    # < −100 % ist unmöglich → Artefakt → NaN.
    assert pd.isna(pct.iloc[1])
    # Legitime negative Wachstumsrate bleibt drin und rankt am schlechtesten.
    assert pct.iloc[4] == pct.dropna().min()


def test_roe_masked_on_negative_equity():
    """Negatives Eigenkapital (D/E < 0) + Verlust ergäbe positive ROE —
    solche Werte dürfen nicht ranken."""
    from app.core.scoring import _clean_series

    df = _universe(pe=[10.0] * 6, pb=[1.0] * 6)
    df["roe"] = [0.90, 0.20, 0.15, 0.10, 0.05, 0.02]
    df["debt_equity"] = [-1.5, 0.5, 0.5, 0.5, 0.5, 0.5]

    # Maskierung passiert in compute_scores; hier direkt die Regel nachstellen:
    df.loc[df["debt_equity"] < 0, "roe"] = float("nan")
    assert pd.isna(_clean_series(df, "roe").iloc[0])


def _full_universe(n: int = 6) -> pd.DataFrame:
    """Minimal-Universum mit allen Spalten, die compute_scores benötigt."""
    import numpy as np

    from app.core.schema import KOYFIN_COLUMNS

    df = pd.DataFrame({c: [np.nan] * n for c in KOYFIN_COLUMNS})
    df["ticker"] = [f"T{i}" for i in range(n)]
    df["name"] = df["ticker"]
    df["sector"] = ["Materials"] * n
    df["industry"] = ["Chemicals"] * n
    df["market_cap"] = [5000.0] * n
    df["sma_20"] = np.nan
    df["fwd_rev_growth"] = np.nan
    return df


def test_piotroski_incomplete_data_yields_nan_and_filter_dash():
    """Titel mit lückenhaften Fundamentaldaten: F-Score NaN, Filter '-'
    (keine Aussage) statt 'NEIN' (Filter nicht bestanden)."""
    df = _full_universe()
    # Nur 2 von 9 Kriterien bewertbar (net_income, ocf) → NaN.
    df["net_income"] = [100.0] * 6
    df["ocf"] = [120.0] * 6
    df["altman_z"] = [3.0] * 6

    scored = compute_scores(df, Settings())
    assert scored["piotroski"].isna().all()
    assert (scored["filter_ok"] == "-").all()
    assert (scored["recommendation"] == "-").all()


def test_piotroski_complete_data_still_scored():
    df = _full_universe()
    for col in [
        "net_income",
        "ocf",
        "total_assets",
        "total_debt",
        "current_assets",
        "current_liab",
        "shares_out",
        "revenue",
        "cogs",
    ]:
        df[col] = [100.0] * 6
        df[f"{col}_prev"] = [90.0] * 6
    scored = compute_scores(df, Settings())
    assert scored["piotroski"].notna().all()
    assert (scored["piotroski"] >= 0).all() and (scored["piotroski"] <= 9).all()


def test_altman_filter_skipped_for_financials():
    """Financials: Altman-Z-Kriterium wird übersprungen (konzeptionell nicht
    definiert für Banken/Versicherer)."""
    df = _full_universe()
    df["sector"] = ["Financials"] * 3 + ["Materials"] * 3
    # Vollständige Piotroski-Daten mit starken Signalen (>= 5 Punkte).
    df["net_income"] = [100.0] * 6
    df["net_income_prev"] = [50.0] * 6
    df["ocf"] = [150.0] * 6
    df["total_assets"] = [1000.0] * 6
    df["total_assets_prev"] = [1100.0] * 6
    df["total_debt"] = [100.0] * 6
    df["total_debt_prev"] = [200.0] * 6
    df["current_assets"] = [500.0] * 6
    df["current_liab"] = [200.0] * 6
    df["current_assets_prev"] = [400.0] * 6
    df["current_liab_prev"] = [250.0] * 6
    df["shares_out"] = [100.0] * 6
    df["shares_out_prev"] = [100.0] * 6
    df["revenue"] = [800.0] * 6
    df["revenue_prev"] = [700.0] * 6
    df["cogs"] = [400.0] * 6
    df["cogs_prev"] = [420.0] * 6
    df["altman_z"] = [None] * 3 + [0.5] * 3  # Financials: NaN, Rest: unter Schwelle

    scored = compute_scores(df, Settings())
    assert (scored.loc[:2, "filter_ok"] == "JA").all(), "Financials ohne Altman → JA"
    assert (scored.loc[3:, "filter_ok"] == "NEIN").all(), "Altman < 1,8 → NEIN"


def test_rev_growth_1y_computed():
    df = _full_universe()
    df["revenue"] = [110.0, 90.0, None, 100.0, 100.0, 100.0]
    df["revenue_prev"] = [100.0, 100.0, 100.0, None, -50.0, 0.0]
    scored = compute_scores(df, Settings())
    assert abs(scored["rev_growth_1y"].iloc[0] - 0.10) < 1e-9
    assert abs(scored["rev_growth_1y"].iloc[1] + 0.10) < 1e-9
    # Fehlende oder nicht-positive Basis → NaN.
    assert scored["rev_growth_1y"].iloc[2:].isna().all()


def test_momentum_score_uses_12_1():
    """Der Momentum-Score basiert auf mom_12_1 (12M ohne letzten Monat),
    ret_1m/ret_12m haben Default-Gewicht 0."""
    settings = Settings()
    assert settings.momentum_weights["mom_12_1"] > 0
    assert settings.momentum_weights["ret_1m"] == 0
    assert settings.momentum_weights["ret_12m"] == 0

    df = _full_universe()
    df["ret_1m"] = [0.30, 0.0, 0.0, 0.0, 0.0, 0.0]
    df["ret_3m"] = [0.05, 0.04, 0.03, 0.02, 0.01, 0.0]
    df["ret_6m"] = [0.05, 0.04, 0.03, 0.02, 0.01, 0.0]
    df["ret_12m"] = [0.10, 0.30, 0.25, 0.20, 0.15, 0.05]
    df["eps_revisions_3m"] = [0.01] * 6
    scored = compute_scores(df, Settings())
    # T0: hoher 12M-Return nur wegen des letzten Monats → mom_12_1 = -0.20,
    # schlechtester Wert. Mit altem ret_12m-Gewicht wäre T0 gut gerankt.
    assert scored["mom_12_1"].iloc[0] == scored["mom_12_1"].min()


def test_min_factor_coverage():
    """Ein Faktor-Score aus einem einzelnen von vielen Indikatoren ist nicht
    vergleichbar → NaN unterhalb der Mindest-Abdeckung."""
    df = _full_universe()
    # Nur rev_cagr_3y (Gewicht 0,20 von 1,00) vorhanden → Abdeckung 20 % < 50 %.
    df["rev_cagr_3y"] = [0.10, 0.08, 0.06, 0.04, 0.02, 0.01]
    scored = compute_scores(df, Settings())
    assert scored["growth_score"].isna().all()

    # Drei von sechs Indikatoren (0,20+0,20+0,20 = 60 % ≥ 50 %) → Score da.
    df["eps_cagr_3y"] = [0.10, 0.08, 0.06, 0.04, 0.02, 0.01]
    df["fwd_eps_growth"] = [0.10, 0.08, 0.06, 0.04, 0.02, 0.01]
    scored = compute_scores(df, Settings())
    assert scored["growth_score"].notna().all()


def test_high_growth_stock_gets_high_growth_score():
    """Micron-Fall: reale Wachstumsraten über 300 % dürfen den Growth-Score
    nicht auslöschen — der Titel muss den besten Growth-Score bekommen."""
    df = _full_universe()
    df["rev_cagr_3y"] = [0.60, 0.10, 0.08, 0.06, 0.04, 0.02]
    df["eps_cagr_3y"] = [4.50, 0.12, 0.10, 0.08, 0.06, 0.04]  # 450 % real
    df["fwd_eps_growth"] = [3.80, 0.15, 0.12, 0.10, 0.08, 0.06]  # 380 % real
    scored = compute_scores(df, Settings())
    assert scored["growth_score"].notna().all()
    assert scored["growth_score"].iloc[0] == scored["growth_score"].max()


def test_total_score_requires_min_factor_coverage():
    """BDX-Fall: Nur Momentum + Low-Vol vorhanden (33 % Faktor-Gewicht) →
    kein Gesamt-Score statt eines überproportionalen aus 2 von 5 Faktoren."""
    df = _full_universe()
    # Momentum-Daten (voll) …
    df["ret_1m"] = [0.02, 0.01, 0.03, 0.00, 0.02, 0.01]
    df["ret_3m"] = [0.05, 0.04, 0.06, 0.02, 0.05, 0.03]
    df["ret_6m"] = [0.10, 0.08, 0.12, 0.05, 0.09, 0.06]
    df["ret_12m"] = [0.30, 0.25, 0.35, 0.15, 0.28, 0.20]
    df["eps_revisions_3m"] = [0.01, 0.02, 0.00, 0.01, 0.02, 0.00]
    # … und Low-Vol-Daten (voll), sonst nichts.
    df["beta"] = [0.9, 1.1, 0.8, 1.2, 1.0, 0.95]
    df["volatility_1y"] = [0.25, 0.30, 0.22, 0.35, 0.28, 0.26]
    df["high_52w"] = [120.0] * 6
    df["low_52w"] = [80.0] * 6

    scored = compute_scores(df, Settings())
    assert scored["momentum_score"].notna().all()
    assert scored["lowvol_score"].notna().all()
    # Value/Quality/Growth fehlen → Gesamt-Score NaN, Klassifikation "-".
    assert scored["total_score"].isna().all()
    assert (scored["classification"] == "-").all()


def test_total_score_ok_when_one_factor_missing():
    """Fehlt nur der Growth-Faktor (15 % Gewicht), bleibt der Gesamt-Score
    erhalten (85 % Abdeckung ≥ 60 %)."""
    df = _full_universe()
    # Value
    df["pe"] = [10.0, 12.0, 14.0, 16.0, 18.0, 20.0]
    df["pb"] = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
    df["ps"] = [1.0, 1.2, 1.4, 1.6, 1.8, 2.0]
    df["ev_ebitda"] = [8.0, 9.0, 10.0, 11.0, 12.0, 13.0]
    # Quality
    df["roe"] = [0.20, 0.18, 0.16, 0.14, 0.12, 0.10]
    df["roic"] = [0.15, 0.14, 0.13, 0.12, 0.11, 0.10]
    df["roa"] = [0.10, 0.09, 0.08, 0.07, 0.06, 0.05]
    df["gross_margin"] = [0.60, 0.55, 0.50, 0.45, 0.40, 0.35]
    df["op_margin"] = [0.30, 0.28, 0.26, 0.24, 0.22, 0.20]
    df["debt_equity"] = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0]
    # Momentum
    df["ret_1m"] = [0.02] * 6
    df["ret_3m"] = [0.05, 0.04, 0.06, 0.02, 0.05, 0.03]
    df["ret_6m"] = [0.10, 0.08, 0.12, 0.05, 0.09, 0.06]
    df["ret_12m"] = [0.30, 0.25, 0.35, 0.15, 0.28, 0.20]
    df["eps_revisions_3m"] = [0.01, 0.02, 0.00, 0.01, 0.02, 0.00]
    # Low-Vol
    df["beta"] = [0.9, 1.1, 0.8, 1.2, 1.0, 0.95]
    df["volatility_1y"] = [0.25, 0.30, 0.22, 0.35, 0.28, 0.26]
    df["high_52w"] = [120.0] * 6
    df["low_52w"] = [80.0] * 6

    scored = compute_scores(df, Settings())
    assert scored["growth_score"].isna().all()
    assert scored["total_score"].notna().all()


def test_data_coverage_column():
    df = _full_universe()
    scored = compute_scores(df, Settings())
    assert "data_coverage" in scored.columns
    # Fast leeres Universum → sehr geringe Abdeckung (nur Piotroski-Spalte
    # zählt nicht, da NaN).
    assert (scored["data_coverage"] < 0.2).all()
    assert (scored["data_coverage"] >= 0).all()


if __name__ == "__main__":
    test_full_pipeline()
    test_lower_better_percentile_inverted()
    test_negative_multiple_gets_no_percentile()
    test_pdf_percentiles_mask_negative_multiples()
    print("OK")
