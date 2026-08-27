"""Piotroski F-Score (9 Kriterien, 0–9 Punkte).

Spalten-Mapping folgt Sheet ``Piotroski`` des Originalmodells.

Fehlende Daten zählen nicht als "Kriterium verfehlt": Jedes Kriterium gilt nur
als bewertbar, wenn seine Eingangsgrößen vorliegen. Sind weniger als
``MIN_VALID_CRITERIA`` Kriterien bewertbar, ist der F-Score NaN — sonst würde
ein Titel mit Datenlücken fälschlich mit 0 Punkten am Filter scheitern, obwohl
über seine Qualität nichts bekannt ist.

Financials (Banken/Versicherer) werden auf einer 6-Kriterien-Variante
gescort: p5 (Verschuldung gesunken — Einlagen/Verbindlichkeiten sind bei
Banken Betriebsmittel, kein Warnsignal), p6 (Current Ratio — für Banken nicht
definiert) und p8 (Bruttomarge — Banken haben keine COGS) sind sachfremd und
gelten als nicht bewertbar, selbst wenn der Export Werte liefert. Der
Maximal-Score einer Bank ist damit 6; ``piotroski_max_criteria`` weist die
Skala je Zeile aus, damit die Filter-Schwelle proportional skaliert werden
kann (siehe ``scoring._filter_row``).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import FINANCIAL_SECTOR_MARKER

# Mindestanzahl bewertbarer Kriterien, damit der F-Score als aussagekräftig
# gilt. Darunter: NaN → der Filter liefert "-" (keine Aussage) statt "NEIN".
MIN_VALID_CRITERIA: int = 6
# Financials haben nur 6 anwendbare Kriterien — Mindestanzahl proportional
# abgesenkt (6/9 ≈ 2/3 der Skala, analog 4 von 6).
MIN_VALID_CRITERIA_FINANCIAL: int = 4

PIOTROSKI_MAX_CRITERIA: int = 9
FINANCIAL_PIOTROSKI_MAX_CRITERIA: int = 6


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return np.where(b > 0, a / b, np.nan)


def is_financial_sector(df: pd.DataFrame) -> pd.Series:
    """Boolesche Maske: Zeile gehört zum GICS-Sektor Financials."""
    if "sector" not in df.columns:
        return pd.Series(False, index=df.index)
    return (
        df["sector"]
        .astype(str)
        .str.lower()
        .str.contains(FINANCIAL_SECTOR_MARKER, na=False)
    )


def compute_piotroski(df: pd.DataFrame) -> pd.DataFrame:
    """Liefert DataFrame mit Spalten ``p1..p9``, ``piotroski`` und
    ``piotroski_max_criteria`` (9, für Financials 6).

    ``piotroski`` ist NaN, wenn weniger als ``MIN_VALID_CRITERIA`` (Financials:
    ``MIN_VALID_CRITERIA_FINANCIAL``) Kriterien bewertbar sind; nicht
    bewertbare Einzelkriterien gehen mit 0 Punkten ein (konservativ).
    """

    out = pd.DataFrame(index=df.index)
    valid = pd.DataFrame(index=df.index)
    financial = is_financial_sector(df)

    out["p1_ni"] = (df["net_income"] > 0).astype(int)
    valid["p1"] = df["net_income"].notna()

    out["p2_ocf"] = (df["ocf"] > 0).astype(int)
    valid["p2"] = df["ocf"].notna()

    roa_now = pd.Series(_safe_div(df["net_income"], df["total_assets"]), index=df.index)
    roa_prev = pd.Series(
        _safe_div(df["net_income_prev"], df["total_assets_prev"]), index=df.index
    )
    out["p3_roa_up"] = (roa_now > roa_prev).astype(int)
    valid["p3"] = roa_now.notna() & roa_prev.notna()

    out["p4_ocf_gt_ni"] = (df["ocf"] > df["net_income"]).astype(int)
    valid["p4"] = df["ocf"].notna() & df["net_income"].notna()

    # p5/p6/p8 sind für Financials sachfremd (siehe Modul-Docstring) und
    # werden dort weder gewertet noch als bewertbar gezählt.
    out["p5_debt_down"] = (
        (df["total_debt"] < df["total_debt_prev"]) & ~financial
    ).astype(int)
    valid["p5"] = df["total_debt"].notna() & df["total_debt_prev"].notna() & ~financial

    cr_now = pd.Series(_safe_div(df["current_assets"], df["current_liab"]), index=df.index)
    cr_prev = pd.Series(
        _safe_div(df["current_assets_prev"], df["current_liab_prev"]), index=df.index
    )
    out["p6_current_ratio_up"] = ((cr_now > cr_prev) & ~financial).astype(int)
    valid["p6"] = cr_now.notna() & cr_prev.notna() & ~financial

    out["p7_shares_stable"] = (df["shares_out"] <= df["shares_out_prev"]).astype(int)
    valid["p7"] = df["shares_out"].notna() & df["shares_out_prev"].notna()

    gm_now = pd.Series(
        _safe_div(df["revenue"] - df["cogs"], df["revenue"]), index=df.index
    )
    gm_prev = pd.Series(
        _safe_div(df["revenue_prev"] - df["cogs_prev"], df["revenue_prev"]),
        index=df.index,
    )
    out["p8_gm_up"] = ((gm_now > gm_prev) & ~financial).astype(int)
    valid["p8"] = gm_now.notna() & gm_prev.notna() & ~financial

    at_now = pd.Series(_safe_div(df["revenue"], df["total_assets"]), index=df.index)
    at_prev = pd.Series(
        _safe_div(df["revenue_prev"], df["total_assets_prev"]), index=df.index
    )
    out["p9_at_up"] = (at_now > at_prev).astype(int)
    valid["p9"] = at_now.notna() & at_prev.notna()

    score = out.iloc[:, :9].sum(axis=1).astype(float)
    out["piotroski_valid_criteria"] = valid.sum(axis=1).astype(int)
    out["piotroski_max_criteria"] = np.where(
        financial, FINANCIAL_PIOTROSKI_MAX_CRITERIA, PIOTROSKI_MAX_CRITERIA
    )
    min_valid = np.where(
        financial, MIN_VALID_CRITERIA_FINANCIAL, MIN_VALID_CRITERIA
    )
    out["piotroski"] = score.where(out["piotroski_valid_criteria"] >= min_valid)
    return out
