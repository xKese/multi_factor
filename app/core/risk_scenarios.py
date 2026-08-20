"""Historische Szenario-Replays und hypothetische Faktor-Schocks.

**Replay (Modul 4):** Das aktuelle Portfolio (heutige Gewichte) wird durch
historische Krisenfenster gespielt. Titel ohne (ausreichende) Kurshistorie
im Fenster — z. B. spätere IPOs — werden ausgeschlossen; die Gewichte der
verfügbaren Titel werden renormalisiert und der Abdeckungsgrad (Summe der
ursprünglichen Gewichte der verfügbaren Titel) prominent ausgewiesen.
Unter ``MIN_COVERAGE`` gilt das Szenario als „nicht belastbar".

**Faktor-Schocks (Modul 5):** Je Titel eine Mehrfachregression der
wöchentlichen Renditen (3 Jahre) auf Benchmark-Rendite, Änderung der
10Y-Treasury-Rendite (in bp), WTI-Rendite und EURUSD-Rendite
(``np.linalg.lstsq`` mit Intercept; der Intercept wird bei der
Schock-Propagation bewusst nicht mitgenommen). R² wird je Regression
gespeichert; Titel mit R² < ``MIN_R2`` sind als „geringe Erklärungsgüte"
markiert — die Schock-P&L solcher Titel ist entsprechend unsicher.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import numpy as np
import pandas as pd

from .risk_metrics import daily_returns, portfolio_returns

# Unter diesem Abdeckungsgrad ist ein Replay „nicht belastbar".
MIN_COVERAGE = 0.6
# Mindestanteil der Fenstertage, an denen ein Titel Kurse haben muss,
# um im Replay als verfügbar zu gelten (verhindert Teil-Historien-Bias).
_MIN_TITLE_WINDOW_COVERAGE = 0.9
# Regressions-Setup Faktor-Schocks.
FACTOR_YEARS = 3
MIN_WEEKLY_OBS = 60
MIN_R2 = 0.2

FACTOR_COLUMNS = ["markt", "zins_bp", "oel", "usd"]


@dataclass
class ScenarioResult:
    name: str
    start: date
    ende: date
    coverage: float
    belastbar: bool
    pf_rendite: float
    bm_rendite: float
    aktiv: float
    max_drawdown: float
    schlechtester_tag: date | None
    schlechtester_tag_rendite: float
    fehlende: list[str] = field(default_factory=list)
    n_titel: int = 0
    n_verfuegbar: int = 0
    hinweis: str = ""


def replay_scenario(
    prices_eur: pd.DataFrame,
    benchmark: pd.Series,
    weights: dict[str, float],
    name: str,
    start: date,
    end: date,
) -> ScenarioResult:
    """Replay eines Fensters mit heutigen Gewichten (täglich rebalanced)."""

    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    bm_window = benchmark.loc[start_ts:end_ts].dropna()
    tickers = [t for t in weights if t in prices_eur.columns]
    missing_cols = [t for t in weights if t not in prices_eur.columns]

    if len(bm_window) < 2:
        return ScenarioResult(
            name=name,
            start=start,
            ende=end,
            coverage=0.0,
            belastbar=False,
            pf_rendite=float("nan"),
            bm_rendite=float("nan"),
            aktiv=float("nan"),
            max_drawdown=float("nan"),
            schlechtester_tag=None,
            schlechtester_tag_rendite=float("nan"),
            fehlende=sorted(weights),
            n_titel=len(weights),
            n_verfuegbar=0,
            hinweis="Keine Benchmark-Historie im Fenster.",
        )

    window = prices_eur.loc[bm_window.index, tickers]
    min_days = int(len(bm_window) * _MIN_TITLE_WINDOW_COVERAGE)
    available = [t for t in tickers if window[t].count() >= min_days]
    missing = sorted(set(weights) - set(available))

    total_weight = float(sum(weights.values()))
    coverage = (
        float(sum(weights[t] for t in available)) / total_weight
        if total_weight > 0
        else 0.0
    )

    if not available:
        return ScenarioResult(
            name=name,
            start=bm_window.index[0].date(),
            ende=bm_window.index[-1].date(),
            coverage=0.0,
            belastbar=False,
            pf_rendite=float("nan"),
            bm_rendite=float("nan"),
            aktiv=float("nan"),
            max_drawdown=float("nan"),
            schlechtester_tag=None,
            schlechtester_tag_rendite=float("nan"),
            fehlende=missing,
            n_titel=len(weights),
            n_verfuegbar=0,
            hinweis="Kein Titel mit Kurshistorie im Fenster.",
        )

    # Renormalisierung auf die verfügbaren Titel.
    sub_total = float(sum(weights[t] for t in available))
    renorm = {t: weights[t] / sub_total for t in available}

    rets = daily_returns(window[available])
    pf = portfolio_returns(rets, renorm).dropna()
    bm_rets = bm_window.pct_change(fill_method=None).dropna()

    pf_cum = float((1.0 + pf).prod() - 1.0)
    bm_cum = float((1.0 + bm_rets).prod() - 1.0)
    wealth = (1.0 + pf).cumprod()
    # Basislinie 1,0 (Vermögen vor dem ersten Fenstertag) gehört ins
    # laufende Maximum — sonst fehlt der Drawdown des ersten Abschnitts.
    running_max = wealth.cummax().clip(lower=1.0)
    max_dd = float((wealth / running_max - 1.0).min()) if len(pf) else float("nan")
    worst_day = pf.idxmin() if len(pf) else None

    hinweis = ""
    if missing_cols:
        hinweis = "Titel ohne Kursdaten im Cache: " + ", ".join(missing_cols)

    return ScenarioResult(
        name=name,
        start=bm_window.index[0].date(),
        ende=bm_window.index[-1].date(),
        coverage=coverage,
        belastbar=coverage >= MIN_COVERAGE,
        pf_rendite=pf_cum,
        bm_rendite=bm_cum,
        aktiv=pf_cum - bm_cum,
        max_drawdown=max_dd,
        schlechtester_tag=worst_day.date() if worst_day is not None else None,
        schlechtester_tag_rendite=float(pf.min()) if len(pf) else float("nan"),
        fehlende=missing,
        n_titel=len(weights),
        n_verfuegbar=len(available),
        hinweis=hinweis,
    )


def weekly_factor_panel(
    prices_eur: pd.DataFrame,
    benchmark: pd.Series,
    macro: pd.DataFrame,
    years: int = FACTOR_YEARS,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Wochenpanel (Freitagsstände) für die Schock-Regressionen.

    Liefert ``(wochenrenditen_je_titel, faktoren)`` mit den Faktoren
    ``markt`` (Benchmark-Wochenrendite), ``zins_bp`` (Δ 10Y-Yield in
    Basispunkten; die Quelle liefert Prozentpunkte → ×100), ``oel``
    (WTI-Wochenrendite) und ``usd`` (EURUSD-Wochenrendite, d. h. USD-Stärke
    negativ). Makro-Reihen werden vor dem Wochenraster forward-gefillt
    (eigene Feiertagskalender).
    """

    stock_weekly = (
        prices_eur.resample("W-FRI").last().pct_change(fill_method=None)
    )
    bm_weekly = benchmark.resample("W-FRI").last().pct_change(fill_method=None)

    macro_weekly = macro.ffill().resample("W-FRI").last()
    factors = pd.DataFrame(
        {
            "markt": bm_weekly,
            "zins_bp": macro_weekly["y10"].diff() * 100.0,
            "oel": macro_weekly["wti"].pct_change(fill_method=None),
            "usd": macro_weekly["eurusd"].pct_change(fill_method=None),
        }
    )

    n_weeks = years * 52
    return stock_weekly.tail(n_weeks), factors.tail(n_weeks)


def estimate_betas(
    weekly_returns: pd.DataFrame, factors: pd.DataFrame
) -> pd.DataFrame:
    """OLS-Betas je Titel auf die vier Faktoren (mit Intercept).

    Liefert je Titel ``beta_markt``, ``beta_zins_bp`` (Rendite je bp),
    ``beta_oel``, ``beta_usd``, ``r2``, ``n_obs`` und ``geringe_guete``
    (R² < MIN_R2). Titel mit weniger als ``MIN_WEEKLY_OBS`` gemeinsamen
    Wochen bekommen NaN-Betas und zählen als geringe Güte.
    """

    rows: list[dict] = []
    fac = factors[FACTOR_COLUMNS].dropna()
    for ticker in weekly_returns.columns:
        joined = pd.concat(
            [weekly_returns[ticker].rename("y"), fac], axis=1
        ).dropna()
        row: dict = {"ticker": ticker, "n_obs": int(len(joined))}
        if len(joined) < MIN_WEEKLY_OBS:
            row.update(
                {f"beta_{c}": float("nan") for c in FACTOR_COLUMNS}
            )
            row.update({"r2": float("nan"), "geringe_guete": True})
            rows.append(row)
            continue
        y = joined["y"].to_numpy()
        X = np.column_stack(
            [np.ones(len(joined))] + [joined[c].to_numpy() for c in FACTOR_COLUMNS]
        )
        coef, _, _, _ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ coef
        ss_tot = float(((y - y.mean()) ** 2).sum())
        r2 = 1.0 - float((resid**2).sum()) / ss_tot if ss_tot > 0 else float("nan")
        for i, c in enumerate(FACTOR_COLUMNS):
            row[f"beta_{c}"] = float(coef[i + 1])
        row["r2"] = r2
        row["geringe_guete"] = bool(np.isnan(r2) or r2 < MIN_R2)
        rows.append(row)
    return pd.DataFrame(rows)


def apply_shocks(
    betas: pd.DataFrame,
    weights: dict[str, float],
    shocks: dict[str, dict[str, float]],
) -> pd.DataFrame:
    """Propagiert Schock-Szenarien über die Betas auf das Portfolio.

    Je Szenario: Titel-P&L = Σ beta_f × Schock_f (Intercept bewusst außen
    vor — es geht um den Schock-Effekt, nicht um Alpha). Der Benchmark
    reagiert definitionsgemäß mit Beta 1 auf den Markt-Schock und 0 auf
    alles andere; ``aktiv`` = Portfolio − Benchmark. Titel ohne Betas
    werden ausgeschlossen; ``abdeckung`` weist ihren Gewichtsanteil aus.
    """

    indexed = betas.set_index("ticker") if "ticker" in betas.columns else betas
    usable = [
        t
        for t in weights
        if t in indexed.index and not pd.isna(indexed.loc[t, "beta_markt"])
    ]
    total = float(sum(weights.values()))
    coverage = (
        float(sum(weights[t] for t in usable)) / total if total > 0 else 0.0
    )
    sub_total = float(sum(weights[t] for t in usable))

    rows: list[dict] = []
    for name, shock in shocks.items():
        pf_pnl = 0.0
        for t in usable:
            b = indexed.loc[t]
            title_pnl = (
                float(b["beta_markt"]) * shock.get("markt", 0.0)
                + float(b["beta_zins_bp"]) * shock.get("zins_bp", 0.0)
                + float(b["beta_oel"]) * shock.get("oel", 0.0)
                + float(b["beta_usd"]) * shock.get("usd", 0.0)
            )
            pf_pnl += weights[t] / sub_total * title_pnl if sub_total > 0 else 0.0
        bm_pnl = shock.get("markt", 0.0)
        n_low = int(
            sum(1 for t in usable if bool(indexed.loc[t, "geringe_guete"]))
        )
        rows.append(
            {
                "szenario": name,
                "pf_pnl": pf_pnl if usable else float("nan"),
                "bm_pnl": bm_pnl,
                "aktiv": (pf_pnl - bm_pnl) if usable else float("nan"),
                "abdeckung": coverage,
                "n_geringe_guete": n_low,
            }
        )
    return pd.DataFrame(rows)
