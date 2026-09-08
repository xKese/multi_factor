"""Faktor-Exposures: Fama-French-5 plus Momentum (Spec 8).

Regression in USD (die Faktoren sind USD): tägliche USD-Überschussrendite
auf Mkt-RF, SMB, HML, RMW, CMA, Mom mit Newey-West-Standardfehlern
(5 Lags). Ausgabe: Koeffizienten, t-Statistiken, Alpha p. a., R².
"""

from __future__ import annotations

import io
import math
import re
import zipfile

import numpy as np
import pandas as pd

FACTORS: tuple[str, ...] = ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "Mom")
EXPECTED_SIGN: dict[str, int] = {"HML": 1, "RMW": 1, "CMA": 1, "Mom": 1, "SMB": 0}
NW_LAGS = 5
TRADING_DAYS = 252


def parse_french_csv(content: bytes | str) -> pd.DataFrame:
    """Kenneth-French-CSV (auch als ZIP) → DataFrame mit DatetimeIndex
    ``date`` und Faktorspalten als Dezimalanteile (Datei liefert Prozent).
    Es wird nur der tägliche Block gelesen (Zeilen ``YYYYMMDD,…``)."""
    if isinstance(content, bytes) and content[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            name = next(n for n in zf.namelist() if n.lower().endswith(".csv"))
            content = zf.read(name)
    text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    header: list[str] | None = None
    rows: list[list[str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            if rows:
                break  # Ende des Tagesblocks (danach folgen Jahres-/Monatsblöcke)
            continue
        parts = [p.strip() for p in stripped.split(",")]
        if re.fullmatch(r"\d{8}", parts[0]):
            rows.append(parts)
        elif header is None and parts[0] == "" and len(parts) > 1:
            header = parts[1:]
        elif header is None and rows == [] and len(parts) > 1 and not re.fullmatch(r"\d{6}", parts[0]):
            # Kopfzeile ohne führendes Komma (z. B. "Mom" nach Leerzeile).
            candidate = [p for p in parts if p]
            if candidate and all(not re.fullmatch(r"[\d.-]+", c) for c in candidate):
                header = candidate
    if not rows:
        raise ValueError("Keine Tagesdaten in der Faktordatei gefunden")
    n_cols = len(rows[0]) - 1
    if header is None or len(header) != n_cols:
        header = ["Mom"] if n_cols == 1 else [f"f{i}" for i in range(n_cols)]
    df = pd.DataFrame([r[1:] for r in rows], columns=header)
    df.index = pd.to_datetime([r[0] for r in rows], format="%Y%m%d")
    df.index.name = "date"
    df = df.apply(pd.to_numeric, errors="coerce") / 100.0
    df.columns = [c.strip() for c in df.columns]
    if "Mom" not in df.columns and n_cols == 1:
        df.columns = ["Mom"]
    return df


def merge_factors(ff5: pd.DataFrame | None, mom: pd.DataFrame | None) -> pd.DataFrame | None:
    if ff5 is None or ff5.empty:
        return None
    out = ff5.copy()
    if mom is not None and not mom.empty:
        mom_col = "Mom" if "Mom" in mom.columns else mom.columns[0]
        out = out.join(mom[[mom_col]].rename(columns={mom_col: "Mom"}), how="inner")
    return out


def newey_west_tstats(X: np.ndarray, y: np.ndarray, lags: int = NW_LAGS) -> tuple[np.ndarray, np.ndarray, float]:
    """OLS mit Newey-West-HAC-Standardfehlern (Bartlett-Kernel).
    Liefert (Koeffizienten, t-Werte, R²). ``X`` enthält die Konstante."""
    n, k = X.shape
    XtX_inv = np.linalg.pinv(X.T @ X)
    b = XtX_inv @ X.T @ y
    resid = y - X @ b
    S = np.zeros((k, k))
    u = X * resid[:, None]
    S += u.T @ u
    for lag in range(1, lags + 1):
        w = 1.0 - lag / (lags + 1.0)
        gamma = u[lag:].T @ u[:-lag]
        S += w * (gamma + gamma.T)
    cov = XtX_inv @ S @ XtX_inv
    se = np.sqrt(np.clip(np.diag(cov), 0, None))
    t = np.divide(b, se, out=np.full_like(b, np.nan), where=se > 0)
    ss_tot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else float("nan")
    return b, t, r2


def regress(excess: pd.Series, factors: pd.DataFrame, lags: int = NW_LAGS) -> dict:
    """Regression einer täglichen Überschussrendite auf die Faktoren."""
    cols = [c for c in FACTORS if c in factors.columns]
    data = pd.concat([excess.rename("y"), factors[cols]], axis=1).dropna()
    if len(data) < 60:
        return {"n": int(len(data)), "alpha_daily": float("nan"), "alpha_pa": float("nan"),
                "alpha_t": float("nan"), "betas": {}, "tstats": {}, "r2": float("nan")}
    y = data["y"].to_numpy(dtype=float)
    X = np.column_stack([np.ones(len(data)), data[cols].to_numpy(dtype=float)])
    b, t, r2 = newey_west_tstats(X, y, lags)
    return {
        "n": int(len(data)),
        "alpha_daily": float(b[0]),
        "alpha_pa": float(b[0] * TRADING_DAYS),
        "alpha_t": float(t[0]),
        "betas": {c: float(b[i + 1]) for i, c in enumerate(cols)},
        "tstats": {c: float(t[i + 1]) for i, c in enumerate(cols)},
        "r2": float(r2),
    }


def usd_returns(nav_eur: pd.Series, fx_eur_per_usd: pd.Series) -> pd.Series:
    """EUR-NAV → USD-Tagesrenditen (NAV_USD = NAV_EUR / fx)."""
    fx = fx_eur_per_usd.reindex(nav_eur.index).ffill()
    nav_usd = nav_eur / fx
    return nav_usd.pct_change(fill_method=None).dropna()


def run_factor_regressions(
    nav: pd.Series, bm_nav: pd.Series, fx_eur_per_usd: pd.Series, factors: pd.DataFrame | None
) -> dict[str, dict] | None:
    """Regressionen für Portfolio, Benchmark und aktiv (Portfolio − Benchmark)."""
    if factors is None or factors.empty:
        return None
    rf = factors["RF"] if "RF" in factors.columns else pd.Series(0.0, index=factors.index)
    pf = usd_returns(nav, fx_eur_per_usd)
    bm = usd_returns(bm_nav, fx_eur_per_usd)
    rf_aligned = rf.reindex(pf.index).fillna(0.0)
    out = {
        "portfolio": regress(pf - rf_aligned, factors),
        "benchmark": regress(bm - rf.reindex(bm.index).fillna(0.0), factors),
        "active": regress((pf - bm.reindex(pf.index)).dropna(), factors),
    }
    return out


def flag_loading(factor: str, beta_value: float, tstat: float) -> bool:
    """Rot markieren: falsches Vorzeichen oder |t| < 2 (Spec 8)."""
    expected = EXPECTED_SIGN.get(factor)
    if expected is None or beta_value is None or math.isnan(beta_value):
        return False
    if expected == 0:
        return abs(tstat) >= 2  # SMB ≈ 0 erwartet: signifikante Ladung ist auffällig
    wrong_sign = np.sign(beta_value) != expected
    return bool(wrong_sign or abs(tstat) < 2)
