"""Kern-Scoring-Engine für das Multi-Faktor-Modell.

Repliziert die Logik aus Sheet ``Berechnungen`` inklusive:
- Perzentil-Rang je Indikator (Global / Sektor / Industrie mit Fallback)
- Dynamische Neugewichtung bei fehlenden Werten
- Invertierung für "weniger = besser"
- Faktor-Scores, Gesamt-Score, Klassifikation, Filter, Empfehlung
- SMA-50/SMA-200-Signal
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import (
    FINANCIAL_SECTOR_MARKER,
    GROWTH_CLIP_LIMIT,
    GROWTH_MIN_VALID,
    GROWTH_OUTLIER_INVALID,
    NEGATIVE_IS_INVALID,
    Settings,
)
from .momentum import (
    MOMENTUM_DEATH,
    MOMENTUM_DOWN,
    MOMENTUM_GOLDEN,
    MOMENTUM_NONE,
    MOMENTUM_UP,
    classify_momentum,
    classify_trend_phase,
)
from .piotroski import compute_piotroski


_SMA_ICON_LABELS: dict[str, str] = {
    MOMENTUM_GOLDEN: "✓ GOLDEN CROSS",
    MOMENTUM_UP: "● Kurs > SMA-200",
    MOMENTUM_DOWN: "▼ Kurs < SMA-200",
    MOMENTUM_DEATH: "⚠ DEATH CROSS",
    MOMENTUM_NONE: "-",
}


INDICATOR_TO_COLUMN: dict[str, str] = {
    "pb": "pb",
    "pe": "pe",
    "pfcf": "pfcf",
    "ev_ebitda": "ev_ebitda",
    "ps": "ps",
    "peg": "peg",
    "div_yield": "div_yield",
    "roe": "roe",
    "roic": "roic",
    "roa": "roa",
    "gross_margin": "gross_margin",
    "op_margin": "op_margin",
    "debt_equity": "debt_equity",
    "int_coverage": "int_coverage",
    "current_ratio": "current_ratio",
    "piotroski": "piotroski",
    "altman_z": "altman_z",
    "ocf_ni": "ocf_ni",
    "rev_cagr_3y": "rev_cagr_3y",
    "eps_cagr_3y": "eps_cagr_3y",
    "fcf_cagr_3y": "fcf_cagr_3y",
    "fwd_eps_growth": "fwd_eps_growth",
    "fwd_rev_growth": "fwd_rev_growth",
    "rev_growth_1y": "rev_growth_1y",
    "ret_1m": "ret_1m",
    "ret_3m": "ret_3m",
    "ret_6m": "ret_6m",
    "ret_12m": "ret_12m",
    "mom_12_1": "mom_12_1",
    "eps_revisions_3m": "eps_revisions_3m",
    "beta": "beta",
    "volatility_1y": "volatility_1y",
    "range_52w": "range_52w",
}


def _clean_series(df: pd.DataFrame, column: str) -> pd.Series:
    """Maskiert fachlich ungültige Werte auf NaN, damit sie kein Perzentil
    erhalten: negative Multiples (z. B. negativer P/E durch Verlust) sowie
    mathematisch unmögliche Wachstumsraten (< −100 % p. a. = Artefakt aus
    negativer Basis). Sehr hohe positive Wachstumsraten sind real möglich und
    bleiben erhalten — sie werden fürs Ranking lediglich auf
    ``GROWTH_CLIP_LIMIT`` gedeckelt (zählen als "sehr hoch", Top-Rang)."""
    series = df[column]
    if column in NEGATIVE_IS_INVALID:
        series = series.where(series > 0)
    if column in GROWTH_OUTLIER_INVALID:
        series = series.where(series >= GROWTH_MIN_VALID)
        series = series.clip(upper=GROWTH_CLIP_LIMIT)
    return series


def _percentile_rank(series: pd.Series) -> pd.Series:
    """Excel PERCENTRANK.INC – linear interpoliert, NaN bleibt NaN."""
    return series.rank(pct=True, method="average", na_option="keep")


def _grouped_percentile(
    series: pd.Series, group: pd.Series, min_count: int | None = None
) -> pd.Series:
    """Perzentil innerhalb einer Gruppierung.

    Rückgabe ist ``NaN``, falls Gruppe zu klein ist und ``min_count`` gesetzt.
    """
    counts = series.groupby(group).transform(lambda s: s.notna().sum())
    ranks = series.groupby(group).transform(_percentile_rank)
    if min_count is not None:
        ranks = ranks.where(counts >= min_count)
    return ranks


def _indicator_percentile(
    df: pd.DataFrame,
    column: str,
    settings: Settings,
) -> pd.Series:
    """Perzentil-Rang gemäß Modus (mit Industrie→Sektor→Global-Fallback)."""

    series = _clean_series(df, column)
    mode = settings.percentile_mode

    if mode == "Global":
        pct = _percentile_rank(series)
    elif mode == "Sektor":
        pct = _grouped_percentile(series, df["sector"])
        pct = pct.fillna(_percentile_rank(series))
    else:  # Industrie mit Fallback Sektor → Global
        pct = _grouped_percentile(
            series, df["industry"], min_count=settings.min_stocks_per_industry
        )
        sector_pct = _grouped_percentile(series, df["sector"])
        pct = pct.fillna(sector_pct)
        pct = pct.fillna(_percentile_rank(series))

    if column in settings.INVERT_LOW_IS_BETTER:
        pct = 1 - pct
    return pct


def _factor_coverage(df: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """Anteil der Indikator-Gewichtssumme, für den valide Daten vorliegen
    (0..1). Basis ist die volle Gewichtssumme des Faktors — auch wenn eine
    Spalte im Import komplett fehlt, zählt sie als nicht abgedeckt."""

    total = sum(weights.values())
    covered = pd.Series(0.0, index=df.index)
    if total <= 0:
        return covered
    for indicator, weight in weights.items():
        column = INDICATOR_TO_COLUMN[indicator]
        if column not in df.columns:
            continue
        covered = covered + weight * _clean_series(df, column).notna()
    return covered / total


def _factor_score(
    df: pd.DataFrame,
    weights: dict[str, float],
    settings: Settings,
) -> pd.Series:
    """Gewichteter Durchschnitt der Indikator-Perzentile mit dynamischer
    Neugewichtung bei fehlenden Werten.

    Liegt weniger als ``settings.min_factor_coverage`` der Gewichtssumme mit
    Daten vor, ist der Score NaN — ein Faktor-Score aus einem einzelnen
    Indikator wäre nicht mit voll abgedeckten Titeln vergleichbar."""

    score_num = pd.Series(0.0, index=df.index)
    weight_sum = pd.Series(0.0, index=df.index)
    total_weight = sum(weights.values())

    for indicator, weight in weights.items():
        column = INDICATOR_TO_COLUMN[indicator]
        if column not in df.columns:
            continue
        values = _clean_series(df, column)
        pct = _indicator_percentile(df, column, settings)
        mask = values.notna()
        score_num = score_num + pct.fillna(0) * weight * mask
        weight_sum = weight_sum + weight * mask

    min_weight = max(settings.min_factor_coverage * total_weight, 0.0)
    enough = (weight_sum > 0) & (weight_sum >= min_weight)
    score = np.where(enough, score_num / weight_sum.where(weight_sum > 0), np.nan)
    return pd.Series(score, index=df.index)


def _classify(score: float) -> str:
    if pd.isna(score):
        return "-"
    if score >= 80:
        return "A - Exzellent"
    if score >= 70:
        return "B+ - Sehr Gut"
    if score >= 60:
        return "B - Gut"
    if score >= 50:
        return "C - Durchschnitt"
    if score >= 40:
        return "D - Unterdurchschnitt"
    return "F - Schwach"


def _recommendation(score: float, filter_ok: str) -> str:
    if filter_ok == "-":
        return "-"
    if filter_ok == "NEIN":
        return "Filter nicht bestanden"
    if pd.isna(score):
        return "-"
    if score >= 80:
        return "STRONG BUY"
    if score >= 70:
        return "BUY"
    if score >= 50:
        return "HOLD"
    return "SELL"


def _sma_signal(row: pd.Series) -> str:
    state = classify_momentum(
        row.get("last_price"), row.get("sma_50"), row.get("sma_200")
    )
    return _SMA_ICON_LABELS[state]


def compute_scores(df: pd.DataFrame, settings: Settings) -> pd.DataFrame:
    """Führt alle Berechnungen durch und liefert angereicherten DataFrame."""

    df = df.copy()

    # Piotroski vorab, damit als Quality-Indikator nutzbar.
    piotroski = compute_piotroski(df)
    df = df.join(piotroski)

    # OCF/NI als zusätzliche Kennzahl (Cash-Qualität), fließt als
    # Quality-Indikator ins Scoring ein.
    df["ocf_ni"] = np.where(df["net_income"] > 0, df["ocf"] / df["net_income"], np.nan)

    # Umsatzwachstum 1J aus den bereits vorhandenen Piotroski-Spalten —
    # kürzerer, aktuellerer Horizont als die 3J-CAGRs, ohne Export-Änderung.
    df["rev_growth_1y"] = np.where(
        df["revenue_prev"] > 0, df["revenue"] / df["revenue_prev"] - 1, np.nan
    )

    # ROE bei negativem Eigenkapital maskieren: Verlust auf negativem
    # Eigenkapital ergäbe eine positive ROE und damit ein falsches
    # Top-Perzentil. Proxy für negatives Eigenkapital ist ein negatives
    # Debt/Equity (wird dort ebenfalls maskiert).
    if "debt_equity" in df.columns and "roe" in df.columns:
        df.loc[df["debt_equity"] < 0, "roe"] = np.nan

    # 12-1-Momentum (12M-Return ohne letzten Monat) — Standard-Definition,
    # die den kurzfristigen Reversal-Effekt ausklammert; wird als
    # Momentum-Indikator gescort und im Momentum-Monitor angezeigt.
    df["mom_12_1"] = df["ret_12m"] - df["ret_1m"]

    # 52-Wochen-Range in %.
    df["range_52w"] = np.where(
        (df["low_52w"] > 0) & df["high_52w"].notna(),
        (df["high_52w"] - df["low_52w"]) / df["low_52w"],
        np.nan,
    )

    # Faktor-Scores (0..1) → 0..100.
    weight_map = settings.factor_weight_map()
    df["value_score"] = _factor_score(df, weight_map["value"], settings) * 100
    df["quality_score"] = _factor_score(df, weight_map["quality"], settings) * 100
    df["growth_score"] = _factor_score(df, weight_map["growth"], settings) * 100
    df["momentum_score"] = _factor_score(df, weight_map["momentum"], settings) * 100
    df["lowvol_score"] = _factor_score(df, weight_map["lowvol"], settings) * 100

    # Daten-Abdeckung (0..1): mit Faktor-Gewichten gewichteter Anteil der
    # Indikator-Gewichtssumme, für den valide Daten vorliegen. Macht sichtbar,
    # auf wie viel Datenbasis ein Score steht.
    fw_total = sum(settings.factor_weights.values())
    if fw_total > 0:
        coverage = pd.Series(0.0, index=df.index)
        for factor, fw in settings.factor_weights.items():
            coverage = coverage + fw * _factor_coverage(df, weight_map[factor])
        df["data_coverage"] = coverage / fw_total
    else:
        df["data_coverage"] = np.nan

    # Gesamt-Score mit dynamischer Neugewichtung auf Faktor-Ebene. Die
    # vorhandenen Faktoren müssen mindestens ``min_total_coverage`` der
    # Faktor-Gewichtssumme stellen — sonst würde ein Titel mit z. B. nur
    # Momentum + Low-Vol einen überproportional hohen, nicht vergleichbaren
    # Gesamt-Score erhalten.
    factor_cols = {
        "value_score": settings.factor_weights["value"],
        "quality_score": settings.factor_weights["quality"],
        "growth_score": settings.factor_weights["growth"],
        "momentum_score": settings.factor_weights["momentum"],
        "lowvol_score": settings.factor_weights["lowvol"],
    }
    num = pd.Series(0.0, index=df.index)
    den = pd.Series(0.0, index=df.index)
    for col, w in factor_cols.items():
        mask = df[col].notna()
        num = num + df[col].fillna(0) * w * mask
        den = den + w * mask
    total_weight = sum(factor_cols.values())
    min_den = settings.min_total_coverage * total_weight
    enough = (den > 0) & (den >= min_den)
    df["total_score"] = np.where(enough, (num / den.where(den > 0)).round(1), np.nan)

    # Klassifikation.
    df["classification"] = df["total_score"].apply(_classify)

    # Filter. Für Financials wird das Altman-Z-Kriterium übersprungen — der
    # Z-Score ist für Banken/Versicherer konzeptionell nicht definiert und
    # würde den Sektor de facto vom Filter ausschließen.
    def _filter_row(r: pd.Series) -> str:
        piotr = r.get("piotroski")
        altman = r.get("altman_z")
        mcap = r.get("market_cap")
        sector = r.get("sector")
        is_financial = (
            isinstance(sector, str) and FINANCIAL_SECTOR_MARKER in sector.lower()
        )
        if pd.isna(piotr) or pd.isna(mcap):
            return "-"
        if not is_financial and pd.isna(altman):
            return "-"
        if (
            piotr >= settings.min_piotroski
            and mcap >= settings.min_market_cap
            and (is_financial or altman >= settings.min_altman_z)
        ):
            return "JA"
        return "NEIN"

    df["filter_ok"] = df.apply(_filter_row, axis=1)

    # Empfehlung.
    df["recommendation"] = df.apply(
        lambda r: _recommendation(r["total_score"], r["filter_ok"]), axis=1
    )

    # SMA-Signal & Abstand.
    df["sma_signal"] = df.apply(_sma_signal, axis=1)
    df["sma_200_distance"] = np.where(
        (df["sma_200"] > 0) & df["last_price"].notna(),
        (df["last_price"] - df["sma_200"]) / df["sma_200"],
        np.nan,
    )
    df["sma_50_distance"] = np.where(
        (df["sma_50"] > 0) & df["last_price"].notna(),
        (df["last_price"] - df["sma_50"]) / df["sma_50"],
        np.nan,
    )

    # Momentum-Monitor: SMA-Gap, 12-1-Momentum, 52W-Hoch-Distanz, Phase.
    df["sma_gap"] = np.where(
        (df["sma_200"] > 0) & df["sma_50"].notna(),
        (df["sma_50"] - df["sma_200"]) / df["sma_200"],
        np.nan,
    )
    df["dist_52w_high"] = np.where(
        (df["high_52w"] > 0) & df["last_price"].notna(),
        df["last_price"] / df["high_52w"] - 1,
        np.nan,
    )
    if "sma_20" in df.columns:
        df["sma_20_distance"] = np.where(
            (df["sma_20"] > 0) & df["last_price"].notna(),
            (df["last_price"] - df["sma_20"]) / df["sma_20"],
            np.nan,
        )
    else:
        df["sma_20_distance"] = np.nan
    df["trend_phase"] = df.apply(
        lambda r: classify_trend_phase(
            r.get("last_price"), r.get("sma_50"), r.get("sma_200"), r.get("ret_1m")
        ),
        axis=1,
    )

    return df
