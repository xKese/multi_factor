"""Piotroski F-Score (9 Kriterien, 0–9 Punkte).

Spalten-Mapping folgt Sheet ``Piotroski`` des Originalmodells.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _safe_div(a: pd.Series, b: pd.Series) -> pd.Series:
    return np.where(b > 0, a / b, np.nan)


def compute_piotroski(df: pd.DataFrame) -> pd.DataFrame:
    """Liefert DataFrame mit Spalten ``p1..p9`` und ``piotroski``."""

    out = pd.DataFrame(index=df.index)

    out["p1_ni"] = (df["net_income"] > 0).astype(int)
    out["p2_ocf"] = (df["ocf"] > 0).astype(int)

    roa_now = _safe_div(df["net_income"], df["total_assets"])
    roa_prev = _safe_div(df["net_income_prev"], df["total_assets_prev"])
    out["p3_roa_up"] = (pd.Series(roa_now) > pd.Series(roa_prev)).astype(int)

    out["p4_ocf_gt_ni"] = (df["ocf"] > df["net_income"]).astype(int)

    out["p5_debt_down"] = (df["total_debt"] < df["total_debt_prev"]).astype(int)

    cr_now = _safe_div(df["current_assets"], df["current_liab"])
    cr_prev = _safe_div(df["current_assets_prev"], df["current_liab_prev"])
    out["p6_current_ratio_up"] = (pd.Series(cr_now) > pd.Series(cr_prev)).astype(int)

    out["p7_shares_stable"] = (df["shares_out"] <= df["shares_out_prev"]).astype(int)

    gm_now = _safe_div(df["revenue"] - df["cogs"], df["revenue"])
    gm_prev = _safe_div(df["revenue_prev"] - df["cogs_prev"], df["revenue_prev"])
    out["p8_gm_up"] = (pd.Series(gm_now) > pd.Series(gm_prev)).astype(int)

    at_now = _safe_div(df["revenue"], df["total_assets"])
    at_prev = _safe_div(df["revenue_prev"], df["total_assets_prev"])
    out["p9_at_up"] = (pd.Series(at_now) > pd.Series(at_prev)).astype(int)

    out["piotroski"] = out.iloc[:, :9].sum(axis=1)
    return out
