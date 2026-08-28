"""Kern-Logik des Factor-Timing-Systems (regelbasierte taktische Allokation).

Aus der Dash-Seite ``app/pages/factor_timing.py`` extrahiert, damit die
Regeln ohne UI testbar sind. Signal-Hierarchie nach Evidenzstärke:

1. **Faktor-Momentum** (am besten belegt; hier als Querschnitts-Proxy aus dem
   geladenen Universum berechenbar, siehe :func:`factor_momentum_from_universe`)
2. **Value-Spread** (Bewertungsabstand des Value-Quintils — nur Anzeige-
   Hinweis, ohne eigene Historie kein belastbarer Tilt)
3. **Makro-Regime** (Business-Cycle-Rotation — schwächere Evidenz, daher
   bewusst kleine Tilts)
4. **Sentiment** (VIX/Credit/Put-Call — schwächste Evidenz, kleinste Tilts)

Alle Tilts zusammen bewegen sich in ±10 %-Größenordnung um die strategischen
Gewichte; Clamp [5 %, 45 %] und Renormierung verhindern Extremallokationen.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

FACTORS: tuple[str, ...] = ("Value", "Quality", "Growth", "Momentum", "Low Volatility")

REGIME_GOLDILOCKS = "GOLDILOCKS"
REGIME_SLOWDOWN = "SLOWDOWN"
REGIME_STAGFLATION = "STAGFLATION"
REGIME_HEATING_UP = "HEATING UP"

REGIMES = (
    REGIME_GOLDILOCKS,
    REGIME_SLOWDOWN,
    REGIME_STAGFLATION,
    REGIME_HEATING_UP,
)

# Regime → Faktor-Tilt-Richtung (−1/0/+1), skaliert mit REGIME_TILT.
REGIME_MATRIX: dict[str, dict[str, int]] = {
    REGIME_GOLDILOCKS: {"Value": 1, "Quality": 0, "Growth": 1, "Momentum": 1, "Low Volatility": -1},
    REGIME_SLOWDOWN:   {"Value": -1, "Quality": 1, "Growth": -1, "Momentum": 0, "Low Volatility": 1},
    REGIME_STAGFLATION: {"Value": 0, "Quality": 1, "Growth": -1, "Momentum": -1, "Low Volatility": 1},
    REGIME_HEATING_UP:  {"Value": 1, "Quality": 0, "Growth": 0, "Momentum": 1, "Low Volatility": -1},
}

REGIME_TILT = 0.04
MOMENTUM_TILT = 0.03
WEIGHT_FLOOR = 0.05
WEIGHT_CAP = 0.45

# PMI-Hysterese: Innerhalb des Bands 49–51 behält das vorherige Regime seine
# Wachstums-Einstufung — sonst kippt die Klassifikation bei jedem Import um
# die 50er-Marke hin und her.
PMI_WEAK = 49.0
PMI_STRONG = 51.0
CPI_HIGH = 3.0

# Sentiment-Schwellen (symmetrisch, siehe sentiment_tilts).
VIX_RISK_OFF = 25.0
VIX_RISK_ON = 15.0
CREDIT_STRESS_BP = 500.0
PCR_FEAR = 1.2
PCR_GREED = 0.7

# Faktor → Score-Spalte in STATE.scored (für das Universums-Momentum).
_FACTOR_SCORE_COLUMNS: dict[str, str] = {
    "Value": "value_score",
    "Quality": "quality_score",
    "Growth": "growth_score",
    "Momentum": "momentum_score",
    "Low Volatility": "lowvol_score",
}

# Mindestanzahl Titel mit Score+Return, damit der Quintils-Spread als
# aussagekräftig gilt.
MIN_UNIVERSE_FOR_MOMENTUM = 20

# Regime, deren Wachstums-Einstufung "schwach" war (für die Hysterese).
_WEAK_GROWTH_REGIMES = {REGIME_SLOWDOWN, REGIME_STAGFLATION}


def _growth_weak(
    pmi: float, cli: float, prev_regime: str | None
) -> bool:
    """Wachstums-Einstufung mit Hysterese-Band um PMI = 50.

    Unter 49 eindeutig schwach, über 51 eindeutig stark (sofern der CLI
    nicht negativ ist); dazwischen entscheidet das vorherige Regime — ohne
    Vorgeschichte gilt die 50er-Marke."""
    if cli < 0:
        return True
    if pmi < PMI_WEAK:
        return True
    if pmi > PMI_STRONG:
        return False
    if prev_regime in _WEAK_GROWTH_REGIMES:
        return True
    if prev_regime in (REGIME_GOLDILOCKS, REGIME_HEATING_UP):
        return False
    return pmi < 50.0


def detect_regime(
    pmi: float,
    pmi_trend: float,
    cli: float,
    cpi: float,
    spread: float | None = None,
    prev_regime: str | None = None,
) -> str:
    """Makro-Regime v2 mit deterministischer Präzedenz.

    - Wachstum schwach (PMI/CLI, mit Hysterese) & CPI > 3 → STAGFLATION —
      **vor** SLOWDOWN geprüft, der klassische Fall "PMI sinkt + Inflation
      hoch" (z. B. 2022) wird sonst falsch klassifiziert.
    - Wachstum schwach sonst → SLOWDOWN.
    - Wachstum stark & CPI > 3 → HEATING UP.
    - Wachstum stark, Trend ≥ 0, CLI > 0, CPI ≤ 3 → GOLDILOCKS; sonst
      HEATING UP (spätzyklische Mischlage).
    - Inverse Zinskurve (10Y−2Y < 0) als Spätzyklus-Dämpfer: GOLDILOCKS
      wird auf HEATING UP heruntergestuft.
    """
    weak = _growth_weak(pmi, cli, prev_regime)
    if weak:
        if cpi > CPI_HIGH:
            return REGIME_STAGFLATION
        return REGIME_SLOWDOWN
    if cpi > CPI_HIGH:
        return REGIME_HEATING_UP
    if pmi_trend >= 0 and cli > 0:
        if spread is not None and spread < 0:
            return REGIME_HEATING_UP
        return REGIME_GOLDILOCKS
    return REGIME_HEATING_UP


def momentum_signal(momenta: dict[str, float]) -> dict[str, str]:
    """Top 2 → ÜBERGEWICHTEN, Bottom 2 → UNTERGEWICHTEN, Mitte NEUTRAL."""
    ranks = sorted(momenta, key=lambda k: momenta[k], reverse=True)
    return {
        f: (
            "ÜBERGEWICHTEN"
            if ranks.index(f) < 2
            else "UNTERGEWICHTEN" if ranks.index(f) >= 3 else "NEUTRAL"
        )
        for f in momenta
    }


def sentiment_tilts(
    vix: float, credit: float, pcr: float | None = None
) -> tuple[dict[str, float], list[str]]:
    """Symmetrische Sentiment-Tilts plus Klartext der gefeuerten Regeln.

    Rückgabe ``(tilts_je_faktor, aktive_regeln)``. Die Regeln sind bewusst
    klein (±1–2 pp) — Sentiment ist die schwächste Signalschicht."""
    tilts = {f: 0.0 for f in FACTORS}
    fired: list[str] = []

    if vix > VIX_RISK_OFF:
        tilts["Low Volatility"] += 0.02
        tilts["Quality"] += 0.01
        fired.append(f"VIX > {VIX_RISK_OFF:.0f} → Low Vol +2 pp, Quality +1 pp")
    elif vix < VIX_RISK_ON and vix > 0:
        tilts["Momentum"] += 0.01
        tilts["Low Volatility"] -= 0.01
        fired.append(f"VIX < {VIX_RISK_ON:.0f} → Momentum +1 pp, Low Vol −1 pp")

    if credit > CREDIT_STRESS_BP:
        tilts["Value"] -= 0.01
        tilts["Quality"] += 0.01
        fired.append(
            f"Credit-OAS > {CREDIT_STRESS_BP:.0f} bp → Value −1 pp, Quality +1 pp"
        )

    if pcr is not None and pcr > 0:
        if pcr > PCR_FEAR:
            # Extreme Angst als Kontra-Signal: Erholungen tragen Momentum.
            tilts["Momentum"] += 0.01
            fired.append(f"Put/Call > {PCR_FEAR} (Extremangst) → Momentum +1 pp")
        elif pcr < PCR_GREED:
            tilts["Low Volatility"] += 0.01
            fired.append(f"Put/Call < {PCR_GREED} (Sorglosigkeit) → Low Vol +1 pp")

    return tilts, fired


def tactical_weights(
    strategic: dict[str, float],
    regime: str,
    mom_signal: dict[str, str],
    sent_tilts: dict[str, float],
) -> dict[str, dict[str, float]]:
    """Taktische Gewichte mit vollständiger Tilt-Zerlegung je Faktor.

    Rückgabe je Faktor: ``{strategic, regime_tilt, momentum_tilt,
    sentiment_tilt, tactical}``. ``tactical`` ist nach Clamp [0.05, 0.45]
    renormalisiert; die Summe der ``tactical``-Werte ist 1,0."""
    raw: dict[str, dict[str, float]] = {}
    for factor, strat in strategic.items():
        regime_tilt = REGIME_MATRIX[regime][factor] * REGIME_TILT
        mom_tilt = (
            MOMENTUM_TILT
            if mom_signal.get(factor) == "ÜBERGEWICHTEN"
            else (-MOMENTUM_TILT if mom_signal.get(factor) == "UNTERGEWICHTEN" else 0.0)
        )
        sent = float(sent_tilts.get(factor, 0.0))
        clamped = max(WEIGHT_FLOOR, min(WEIGHT_CAP, strat + regime_tilt + mom_tilt + sent))
        raw[factor] = {
            "strategic": float(strat),
            "regime_tilt": regime_tilt,
            "momentum_tilt": mom_tilt,
            "sentiment_tilt": sent,
            "tactical": clamped,
        }
    total = sum(v["tactical"] for v in raw.values())
    if total > 0:
        for v in raw.values():
            v["tactical"] = v["tactical"] / total
    return raw


def factor_momentum_from_universe(scored: pd.DataFrame | None) -> dict[str, float]:
    """Faktor-Momentum-Proxy aus dem Querschnitt des geladenen Universums.

    Je Faktor: Mittel des 6M-Returns im Top-Quintil (nach Faktor-Score)
    minus Bottom-Quintil, in Prozentpunkten (×100). Das ist ein
    Ein-Zeitpunkt-Proxy (keine echte Faktor-Return-Zeitreihe), aber
    objektiv reproduzierbar statt einer manuellen Schätzeingabe.

    Faktoren mit weniger als ``MIN_UNIVERSE_FOR_MOMENTUM`` Titeln (Score-
    und Return-Daten vorhanden) fehlen im Ergebnis-Dict."""
    out: dict[str, float] = {}
    if scored is None or scored.empty or "ret_6m" not in scored.columns:
        return out
    for factor, col in _FACTOR_SCORE_COLUMNS.items():
        if col not in scored.columns:
            continue
        sub = scored[[col, "ret_6m"]].dropna()
        if len(sub) < MIN_UNIVERSE_FOR_MOMENTUM:
            continue
        q_hi = sub[col].quantile(0.8)
        q_lo = sub[col].quantile(0.2)
        top = sub.loc[sub[col] >= q_hi, "ret_6m"]
        bottom = sub.loc[sub[col] <= q_lo, "ret_6m"]
        if top.empty or bottom.empty:
            continue
        out[factor] = round(float(top.mean() - bottom.mean()) * 100.0, 1)
    return out


def value_spread(scored: pd.DataFrame | None) -> float | None:
    """Bewertungsabstand des Value-Quintils: Median-P/E des Top-Value-
    Quintils ÷ Median-P/E des Universums (< 1 = Value handelt mit Abschlag).

    Reiner Anzeige-Hinweis — ohne eigene Historie lässt sich daraus kein
    belastbarer Timing-Tilt ableiten (kein z-Score möglich)."""
    if (
        scored is None
        or scored.empty
        or "value_score" not in scored.columns
        or "pe" not in scored.columns
    ):
        return None
    sub = scored[["value_score", "pe"]].dropna()
    sub = sub[sub["pe"] > 0]
    if len(sub) < MIN_UNIVERSE_FOR_MOMENTUM:
        return None
    q_hi = sub["value_score"].quantile(0.8)
    top_pe = sub.loc[sub["value_score"] >= q_hi, "pe"].median()
    uni_pe = sub["pe"].median()
    if not np.isfinite(top_pe) or not np.isfinite(uni_pe) or uni_pe <= 0:
        return None
    return float(top_pe / uni_pe)


# ── Ableitungen aus AV-Makro-Reihen ─────────────────────────────────────────


def spread_from_yields(y10: pd.Series, y2: pd.Series) -> float | None:
    """10Y−2Y in Prozentpunkten am letzten gemeinsamen Handelstag."""
    if y10 is None or y2 is None or y10.empty or y2.empty:
        return None
    joined = pd.concat([y10.rename("y10"), y2.rename("y2")], axis=1).dropna()
    if joined.empty:
        return None
    last = joined.iloc[-1]
    return round(float(last["y10"] - last["y2"]), 2)


def cpi_yoy(cpi_index: pd.Series) -> float | None:
    """CPI-Jahresrate in % aus der monatlichen Index-Reihe (letzter Monat
    vs. 12 Monate zuvor)."""
    if cpi_index is None or cpi_index.dropna().shape[0] < 13:
        return None
    series = cpi_index.dropna().sort_index()
    last = float(series.iloc[-1])
    prev = float(series.iloc[-13])
    if prev <= 0:
        return None
    return round((last / prev - 1.0) * 100.0, 1)
