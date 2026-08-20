"""Ex-post Tracking Error, Kernkennzahlen und aktive Sektorallokation.

Alle Renditen sind einfache Tagesrenditen (nicht logarithmiert) aus
Adjusted-Close-Preisen in EUR. Kennzahlen werden mit 252 Handelstagen
annualisiert. Reine Rechenlogik ohne Dash-/DB-Abhängigkeiten.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

TRADING_DAYS = 252

# Rollierende TE-Fenster in Handelstagen.
TE_WINDOWS: dict[str, int] = {"1J": 252, "3J": 756}

# Mindestanteil vorhandener Beobachtungen, damit eine Fenster-Kennzahl
# ausgewiesen wird (statt einer Zahl aus einer Handvoll Tage).
_MIN_WINDOW_COVERAGE = 0.8

VARIANT_FIXED = "fest"
VARIANT_BUYHOLD = "buyhold"


def daily_returns(prices: pd.DataFrame) -> pd.DataFrame:
    """Einfache Tagesrenditen; Lücken bleiben NaN (kein implizites Fill)."""

    return prices.pct_change(fill_method=None)


def portfolio_returns(
    returns: pd.DataFrame, weights: dict[str, float], variant: str = VARIANT_FIXED
) -> pd.Series:
    """Portfoliorendite aus Einzeltitel-Renditen.

    - ``"fest"`` (Default): fixe Gewichte, täglich rebalanced. An Tagen mit
      fehlenden Einzelwerten wird über die verfügbaren Titel renormalisiert,
      damit ein Datenloch nicht wie eine Nullrendite des Titels wirkt.
    - ``"buyhold"``: Buy-and-Hold ab dem ersten Tag — die Gewichte driften
      mit der Wertentwicklung. Fehlende Renditen zählen als 0 (Position
      unverändert fortgeschrieben).
    """

    cols = [c for c in returns.columns if c in weights]
    if not cols:
        return pd.Series(dtype=float, index=returns.index)
    rets = returns[cols]
    w = pd.Series({c: float(weights[c]) for c in cols})

    if variant == VARIANT_FIXED:
        weighted = rets.mul(w, axis=1)
        available = rets.notna().mul(w, axis=1).sum(axis=1)
        summed = weighted.sum(axis=1, min_count=1)
        out = summed.divide(available.where(available > 0))
        out.name = "portfolio"
        return out

    if variant == VARIANT_BUYHOLD:
        growth = (1.0 + rets.fillna(0.0)).cumprod()
        value = growth.mul(w, axis=1).sum(axis=1)
        base = pd.Series(
            np.concatenate([[float(w.sum())], value.to_numpy()[:-1]]),
            index=value.index,
        )
        out = value / base - 1.0
        # Tage vor der ersten Rendite (komplett NaN im Input) neutralisieren.
        out[rets.isna().all(axis=1)] = np.nan
        out.name = "portfolio"
        return out

    raise ValueError(f"Unbekannte Portfoliovariante: {variant!r}")


def rolling_te(active: pd.Series, window: int) -> pd.Series:
    """Rollierender annualisierter Tracking Error (Std × √252)."""

    min_obs = max(2, int(window * _MIN_WINDOW_COVERAGE))
    return active.rolling(window, min_periods=min_obs).std(ddof=1) * math.sqrt(
        TRADING_DAYS
    )


def _te(active: pd.Series) -> float:
    if active.count() < 2:
        return float("nan")
    return float(active.std(ddof=1) * math.sqrt(TRADING_DAYS))


def max_relative_drawdown(pf: pd.Series, bm: pd.Series) -> float:
    """Maximaler Drawdown des Wertverhältnisses Portfolio/Benchmark."""

    both = pd.concat([pf, bm], axis=1, keys=["pf", "bm"]).dropna()
    if both.empty:
        return float("nan")
    rel = (1.0 + both["pf"]).cumprod() / (1.0 + both["bm"]).cumprod()
    # Basislinie 1,0 (Gleichstand vor dem ersten Tag) zählt zum laufenden
    # Maximum — ein relativer Rückstand ab Tag 1 ist bereits ein Drawdown.
    running_max = rel.cummax().clip(lower=1.0)
    return float((rel / running_max - 1.0).min())


def _capture(pf: pd.Series, bm: pd.Series, upside: bool) -> float:
    mask = bm > 0 if upside else bm < 0
    if not mask.any():
        return float("nan")
    bm_mean = bm[mask].mean()
    if bm_mean == 0:
        return float("nan")
    return float(pf[mask].mean() / bm_mean)


def ex_post_metrics(pf: pd.Series, bm: pd.Series) -> dict:
    """Kennzahlentabelle des Ex-post-Vergleichs Portfolio vs. Benchmark.

    Fenster-TEs (1J/3J) werden nur ausgewiesen, wenn mindestens 80 % der
    Fenstertage vorliegen — sonst NaN statt Scheingenauigkeit.
    """

    both = pd.concat([pf, bm], axis=1, keys=["pf", "bm"]).dropna()
    p, b = both["pf"], both["bm"]
    active = p - b

    out: dict[str, float] = {
        "te_gesamt": _te(active),
        "n_tage": int(len(active)),
    }
    for label, window in TE_WINDOWS.items():
        tail = active.tail(window)
        out[f"te_{label.lower()}"] = (
            _te(tail)
            if len(tail) >= int(window * _MIN_WINDOW_COVERAGE)
            else float("nan")
        )

    te = out["te_gesamt"]
    aktive_rendite_pa = float(active.mean() * TRADING_DAYS)
    out["aktive_rendite_pa"] = aktive_rendite_pa
    out["information_ratio"] = (
        aktive_rendite_pa / te if te and not math.isnan(te) and te > 0 else float("nan")
    )

    if len(both) >= 2 and float(b.var(ddof=1)) > 0:
        out["aktives_beta"] = float(p.cov(b) / b.var(ddof=1))
        out["korrelation"] = float(p.corr(b))
    else:
        out["aktives_beta"] = float("nan")
        out["korrelation"] = float("nan")

    out["upside_capture"] = _capture(p, b, upside=True)
    out["downside_capture"] = _capture(p, b, upside=False)
    out["max_rel_drawdown"] = max_relative_drawdown(p, b)
    if len(both):
        out["start"] = both.index[0].date()
        out["ende"] = both.index[-1].date()
    return out


def active_sector_weights(
    weights: dict[str, float],
    sectors: dict[str, str],
    bm_sector_weights: dict[str, float],
) -> pd.DataFrame:
    """Aktive Sektorgewichte: Portfolio-Aggregat vs. Benchmark-Gewichte.

    Liefert Spalten ``sektor``, ``pf_gewicht``, ``bm_gewicht``, ``aktiv``
    (alles Dezimalanteile), sortiert nach |aktiv| absteigend. Sektoren, die
    nur auf einer Seite vorkommen, erscheinen mit 0 auf der anderen —
    inklusive „Unbekannt" für Titel ohne Sektorzuordnung, damit die Summe
    der Portfolioseite 1,0 bleibt.
    """

    pf: dict[str, float] = {}
    for ticker, weight in weights.items():
        sector = sectors.get(ticker) or "Unbekannt"
        pf[sector] = pf.get(sector, 0.0) + float(weight)

    rows = [
        {
            "sektor": sector,
            "pf_gewicht": pf.get(sector, 0.0),
            "bm_gewicht": float(bm_sector_weights.get(sector, 0.0)),
        }
        for sector in sorted(set(pf) | set(bm_sector_weights))
    ]
    df = pd.DataFrame(rows)
    if df.empty:
        return pd.DataFrame(columns=["sektor", "pf_gewicht", "bm_gewicht", "aktiv"])
    df["aktiv"] = df["pf_gewicht"] - df["bm_gewicht"]
    return (
        df.reindex(df["aktiv"].abs().sort_values(ascending=False).index)
        .reset_index(drop=True)
    )
