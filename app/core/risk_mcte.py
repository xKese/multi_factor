"""Ex-ante Tracking Error und Risikobeiträge je Einzeltitel (MCTE/CTE).

Grundlage sind die aktiven Renditen je Titel (r_i − r_Benchmark) über die
letzten ``COV_WINDOW`` Handelstage. Die Kovarianzmatrix wird mit
Ledoit-Wolf-Shrinkage geschätzt (robuster bei N Titeln ~ T Tagen); die rohe
Sample-Kovarianz wird zusätzlich gerechnet und beide TE-Schätzungen
ausgewiesen (Robustheits-Check — liegen sie weit auseinander, ist die
Schätzung instabil).

Formeln (mit Portfoliogewichten w, Kovarianz Σ der aktiven Tagesrenditen):

- Ex-ante TE (annualisiert) = √(wᵀΣw · 252)
- MCTE_i = (Σw)_i · 252 / TE     (marginaler Beitrag, annualisiert)
- CTE_i  = w_i · MCTE_i          (Komponenten-Beitrag; Σ CTE_i ≡ TE exakt)
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .risk_metrics import TRADING_DAYS

# Schätzfenster der Kovarianz: 2 Jahre Tagesdaten.
COV_WINDOW = 504

# Mindestabdeckung eines Titels im Fenster; darunter fliegt er aus der
# Schätzung (und wird ausgewiesen), statt das gemeinsame Fenster aller
# Titel auf seine kurze Historie zu verkürzen.
_MIN_TITLE_COVERAGE = 0.6


@dataclass
class McteResult:
    te_ledoit_wolf: float
    te_sample: float
    ranking: pd.DataFrame  # ticker, gewicht, mcte, cte, cte_bp (LW-basiert)
    n_tage: int
    shrinkage: float
    # Titel, die mangels Historie nicht in die Schätzung eingehen, plus ihr
    # ursprünglicher Gewichtsanteil (drückt den TE tendenziell nach unten).
    ausgeschlossen: list[str] = field(default_factory=list)
    ausgeschlossen_gewicht: float = 0.0


def _ledoit_wolf_class():
    """Lazy-Import von scikit-learn.

    Bewusst nicht auf Modulebene: Fehlt das Paket (z. B. Umgebung noch ohne
    ``pip install -r requirements.txt``), soll die App trotzdem starten —
    nur die MCTE-Berechnung meldet dann einen verständlichen Hinweis
    (``ValueError`` wird vom Report-Orchestrator als Notiz angezeigt).
    """

    try:
        from sklearn.covariance import LedoitWolf
    except ImportError as exc:
        raise ValueError(
            "scikit-learn ist nicht installiert (für die Ledoit-Wolf-"
            "Kovarianz nötig) — bitte 'pip install -r requirements.txt' "
            "ausführen."
        ) from exc
    return LedoitWolf


def _contributions(
    sigma: np.ndarray, w: np.ndarray
) -> tuple[float, np.ndarray, np.ndarray]:
    """(TE annualisiert, MCTE-Vektor, CTE-Vektor) für eine Kovarianz Σ."""

    var_daily = float(w @ sigma @ w)
    if var_daily <= 0:
        nan = np.full(len(w), np.nan)
        return float("nan"), nan, nan
    te = math.sqrt(var_daily * TRADING_DAYS)
    mcte = (sigma @ w) * TRADING_DAYS / te
    cte = w * mcte
    return te, mcte, cte


def compute_mcte(
    returns: pd.DataFrame,
    bm_returns: pd.Series,
    weights: dict[str, float],
    window: int = COV_WINDOW,
) -> McteResult:
    """Ex-ante TE und Risikobeiträge aus Einzeltitel-Renditen.

    ``returns``: Tagesrenditen je Titel (Spalten = App-Ticker),
    ``bm_returns``: Benchmark-Tagesrenditen auf demselben Kalender.
    Titel mit weniger als 60 % Abdeckung im Fenster werden ausgeschlossen
    und ausgewiesen; die Gewichte der verbleibenden Titel werden auf 1,0
    renormalisiert, damit die Beiträge das investierbare Restportfolio
    beschreiben. Raises ``ValueError`` bei zu dünner Datenlage.
    """

    cols = [c for c in returns.columns if c in weights and weights[c] > 0]
    active = returns[cols].sub(bm_returns, axis=0).tail(window)

    min_obs = int(len(active) * _MIN_TITLE_COVERAGE)
    kept = [c for c in cols if active[c].count() >= max(min_obs, 30)]
    dropped = [c for c in cols if c not in kept]
    dropped_weight = float(sum(weights[c] for c in dropped))
    if len(kept) < 1:
        raise ValueError(
            "Zu wenig Kurshistorie für die Kovarianzschätzung — "
            "bitte zuerst den Kurscache aktualisieren."
        )

    X = active[kept].dropna(how="any")
    if len(X) < 30:
        raise ValueError(
            f"Nur {len(X)} gemeinsame Handelstage im Kovarianzfenster — "
            "Schätzung nicht belastbar."
        )

    total = float(sum(weights[c] for c in kept))
    w = np.array([weights[c] / total for c in kept])

    lw = _ledoit_wolf_class()().fit(X.to_numpy())
    sigma_lw = lw.covariance_
    sigma_sample = np.cov(X.to_numpy().T, ddof=1).reshape(len(kept), len(kept))

    te_lw, mcte_lw, cte_lw = _contributions(sigma_lw, w)
    te_sample, _, _ = _contributions(sigma_sample, w)

    ranking = pd.DataFrame(
        {
            "ticker": kept,
            "gewicht": w,
            "mcte": mcte_lw,
            "cte": cte_lw,
            "cte_bp": cte_lw * 10_000.0,
        }
    ).sort_values("cte", ascending=False, ignore_index=True)

    return McteResult(
        te_ledoit_wolf=te_lw,
        te_sample=te_sample,
        ranking=ranking,
        n_tage=int(len(X)),
        shrinkage=float(lw.shrinkage_),
        ausgeschlossen=dropped,
        ausgeschlossen_gewicht=dropped_weight,
    )


def aggregate_by_sector(
    ranking: pd.DataFrame, sectors: dict[str, str]
) -> pd.DataFrame:
    """CTE-Beiträge auf GICS-Sektorebene (Summe = TE), sortiert absteigend."""

    if ranking.empty:
        return pd.DataFrame(columns=["sektor", "gewicht", "cte", "cte_bp"])
    df = ranking.copy()
    df["sektor"] = [sectors.get(t) or "Unbekannt" for t in df["ticker"]]
    agg = (
        df.groupby("sektor", as_index=False)[["gewicht", "cte", "cte_bp"]]
        .sum()
        .sort_values("cte", ascending=False, ignore_index=True)
    )
    return agg


def join_signals(ranking: pd.DataFrame, scored: pd.DataFrame) -> pd.DataFrame:
    """Reichert das MCTE-Ranking um die Modell-Signale des Bestands an
    (Gesamt-Score, Empfehlung, SMA-Signal, Sektor), damit z. B. „hoher
    TE-Beitrag + SELL + Death Cross" direkt ablesbar ist. Fehlt das
    Universum, kommen die Spalten leer zurück."""

    out = ranking.copy()
    signal_cols = ["total_score", "recommendation", "sma_signal", "sector"]
    if scored is None or scored.empty or "ticker" not in scored.columns:
        for col in signal_cols:
            out[col] = pd.NA
        return out
    cols = ["ticker"] + [c for c in signal_cols if c in scored.columns]
    merged = out.merge(
        scored[cols].drop_duplicates("ticker"), on="ticker", how="left"
    )
    for col in signal_cols:
        if col not in merged.columns:
            merged[col] = pd.NA
    return merged
