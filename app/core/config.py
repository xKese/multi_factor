"""Default-Einstellungen (entsprechen Sheet ``Einstellungen``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

PercentileMode = Literal["Global", "Sektor", "Industrie"]

# Kennzahlen, bei denen ein negativer Wert nur durch einen negativen Nenner
# entsteht (Verlust, negatives EBITDA, negatives Eigenkapital) und daher
# nicht "günstig" bedeutet. Solche Werte werden vor dem Perzentil-Ranking
# auf NaN maskiert, damit sie nach der Inversion kein Top-Perzentil erhalten.
# ``ps`` kann nicht negativ werden, negatives ``beta`` ist legitim.
NEGATIVE_IS_INVALID: frozenset[str] = frozenset(
    {"pe", "pfcf", "peg", "ev_ebitda", "pb", "debt_equity"}
)

# Wachstums-Kennzahlen: Eine Wachstumsrate unter −100 % p. a. ist mathematisch
# unmöglich und damit sicher ein Datenartefakt (z. B. CAGR über negativer
# Basis) → NaN. Sehr hohe positive Raten sind dagegen real möglich (z. B.
# Halbleiter-Zyklus: EPS-Wachstum > 300 %) und dürfen NICHT verworfen werden —
# sie werden beim Ranking lediglich auf GROWTH_CLIP_LIMIT gedeckelt, sodass
# echtes Hochwachstum und etwaige Rechenartefakte gleichermaßen als "sehr
# hoch" (Top-Rang) zählen, statt dass Artefakte allein die Spitze bilden.
GROWTH_MIN_VALID: float = -1.0
GROWTH_CLIP_LIMIT: float = 3.0
GROWTH_OUTLIER_INVALID: frozenset[str] = frozenset(
    {
        "rev_cagr_3y",
        "eps_cagr_3y",
        "fcf_cagr_3y",
        "fwd_eps_growth",
        "fwd_rev_growth",
        "rev_growth_1y",
    }
)

# Sektor-Erkennung für die Altman-Z-Filter-Ausnahme: Der Altman Z-Score ist
# für Banken/Versicherer konzeptionell nicht definiert (Bilanzstruktur), daher
# wird das Filterkriterium für Financials übersprungen. Match per Substring
# auf den Koyfin-GICS-Sektornamen ("Financials").
FINANCIAL_SECTOR_MARKER: str = "financ"


@dataclass
class Settings:
    """Zentrale Konfiguration des Scoring-Modells."""

    factor_weights: dict[str, float] = field(
        default_factory=lambda: {
            "value": 0.25,
            "quality": 0.27,
            "growth": 0.15,
            "momentum": 0.18,
            "lowvol": 0.15,
        }
    )

    value_weights: dict[str, float] = field(
        default_factory=lambda: {
            "pb": 0.15,
            "pe": 0.20,
            "pfcf": 0.15,
            "ev_ebitda": 0.20,
            "ps": 0.10,
            "peg": 0.10,
            "div_yield": 0.10,
        }
    )

    # ``ocf_ni`` (Cash-Conversion) als Earnings-Quality-Indikator,
    # gegenfinanziert aus Interest Coverage und Current Ratio.
    quality_weights: dict[str, float] = field(
        default_factory=lambda: {
            "roe": 0.15,
            "roic": 0.15,
            "roa": 0.10,
            "gross_margin": 0.12,
            "op_margin": 0.12,
            "debt_equity": 0.10,
            "int_coverage": 0.06,
            "current_ratio": 0.05,
            "ocf_ni": 0.05,
            "piotroski": 0.05,
            "altman_z": 0.05,
        }
    )

    # 6 Indikatoren: drei historische (55 %), zwei Forward (35 %) plus
    # kurzfristiges Umsatzwachstum (10 %). ``rev_growth_1y`` wird aus
    # ``revenue``/``revenue_prev`` berechnet; ``fwd_rev_growth`` ist eine
    # optionale Export-Spalte (fehlt sie, greift die dynamische
    # Neugewichtung).
    growth_weights: dict[str, float] = field(
        default_factory=lambda: {
            "rev_cagr_3y": 0.20,
            "eps_cagr_3y": 0.20,
            "fcf_cagr_3y": 0.15,
            "fwd_eps_growth": 0.20,
            "fwd_rev_growth": 0.15,
            "rev_growth_1y": 0.10,
        }
    )

    # ``mom_12_1`` (12M-Return ohne letzten Monat) ersetzt ``ret_12m`` im
    # Score; ``ret_1m`` ist wegen des kurzfristigen Reversal-Effekts auf 0
    # gesetzt. Beide bleiben mit Gewicht 0 gelistet, damit sie in den
    # Einstellungen sichtbar sind und bewusst reaktiviert werden können.
    momentum_weights: dict[str, float] = field(
        default_factory=lambda: {
            "ret_1m": 0.0,
            "ret_3m": 0.25,
            "ret_6m": 0.30,
            "ret_12m": 0.0,
            "mom_12_1": 0.25,
            "eps_revisions_3m": 0.20,
        }
    )

    lowvol_weights: dict[str, float] = field(
        default_factory=lambda: {
            "beta": 0.40,
            "volatility_1y": 0.35,
            "range_52w": 0.25,
        }
    )

    min_piotroski: float = 5.0
    min_altman_z: float = 1.8
    min_market_cap: float = 1000.0
    min_stocks_per_industry: int = 5
    percentile_mode: PercentileMode = "Industrie"
    # Mindest-Datenabdeckung je Faktor: Liegt weniger als dieser Anteil der
    # Indikator-Gewichtssumme mit Daten vor, wird der Faktor-Score NaN (statt
    # eines Scores aus z. B. nur einem Indikator). Der Gesamt-Score wird dann
    # über die Faktor-Neugewichtung aus den übrigen Faktoren gebildet.
    min_factor_coverage: float = 0.5
    # Mindest-Abdeckung auf Faktor-Ebene für den Gesamt-Score: Die Gewichte
    # der vorhandenen Faktor-Scores müssen mindestens diesen Anteil der
    # Faktor-Gewichtssumme stellen, sonst ist der Gesamt-Score NaN. Verhindert,
    # dass ein Titel mit z. B. nur Momentum + Low-Vol (33 % Gewicht) einen
    # überproportional hohen, nicht vergleichbaren Gesamt-Score erhält.
    min_total_coverage: float = 0.6

    # Agenten-Tiefenanalyse (TradingAgents-Service). Leere Strings bedeuten:
    # die Defaults des Service-Katalogs (form_defaults) verwenden.
    agents_provider: str = ""
    agents_quick_model: str = ""
    agents_deep_model: str = ""
    agents_depth: int = 1
    # Sprache der Agenten-Reports (Wire-Wert des Service, z. B. "German").
    agents_language: str = "German"
    # Sampling-Temperatur der Agenten-LLMs (0–2). Default 0: maximal
    # deterministisch/konservativ — für Analyse-Reproduzierbarkeit erwünscht.
    agents_temperature: float = 0.0
    # Kontext der letzten archivierten Analyse (Datum, Rating, Kurzfazit)
    # fließt in Research Manager und Portfolio Manager ein; die Analysten
    # bleiben unbeeinflusst.
    agents_prev_analysis: bool = True

    # ── Risiko & Benchmark ──────────────────────────────────────────────
    # Benchmark-Proxy: iShares MSCI ACWI ETF (US-Listing, USD). Bereits im
    # AV-Symbolformat — wird ohne Mapping direkt geladen.
    risk_benchmark_symbol: str = "ACWI"
    # Zielverzeichnis der Markdown-Reports (relativ zum Arbeitsverzeichnis).
    risk_report_dir: str = "reports"
    # Alpha-Vantage-Rate-Limit (Premium-Plan 75/min → ein Request Puffer).
    risk_av_requests_per_minute: int = 70
    # GICS-Sektorgewichte des MSCI ACWI als Dezimalanteile. Quelle:
    # iShares-ACWI-Factsheet; quartalsweise manuell nachpflegen. Die Keys
    # müssen den Sektornamen des Koyfin-Universums entsprechen — Sektoren
    # ohne Gegenstück werden in der aktiven Allokation mit Gewicht 0 auf
    # der jeweils anderen Seite ausgewiesen (nicht verworfen).
    risk_benchmark_sector_weights: dict[str, float] = field(
        default_factory=lambda: {
            "Information Technology": 0.278,
            "Financials": 0.158,
            "Consumer Discretionary": 0.105,
            "Industrials": 0.105,
            "Health Care": 0.095,
            "Communication Services": 0.082,
            "Consumer Staples": 0.058,
            "Energy": 0.037,
            "Materials": 0.036,
            "Utilities": 0.026,
            "Real Estate": 0.020,
        }
    )
    # Historische Szenario-Fenster (Replay), Werte = [Start, Ende] ISO.
    risk_scenario_windows: dict[str, list[str]] = field(
        default_factory=lambda: {
            "GFC": ["2007-10-09", "2009-03-09"],
            "Eurokrise": ["2011-05-02", "2011-10-04"],
            "COVID": ["2020-02-19", "2020-03-23"],
            "Zinsjahr2022": ["2022-01-03", "2022-10-14"],
            "VolSchock2018": ["2018-01-26", "2018-02-08"],
        }
    )
    # Hypothetische Faktor-Schocks. Keys je Szenario: ``markt`` (Rendite),
    # ``zins_bp`` (Δ 10Y-Treasury in Basispunkten), ``oel`` (WTI-Rendite),
    # ``usd`` (USD-Rendite ggü. EUR). Fehlende Keys = kein Schock.
    risk_factor_shocks: dict[str, dict[str, float]] = field(
        default_factory=lambda: {
            "Zinsen +100bp": {"zins_bp": 100.0},
            "Öl +20%": {"oel": 0.20},
            "USD −10%": {"usd": -0.10},
            "Markt −15%": {"markt": -0.15},
            "Stagflation": {"markt": -0.10, "zins_bp": 75.0, "oel": 0.25},
        }
    )

    INVERT_LOW_IS_BETTER: set[str] = field(
        default_factory=lambda: {
            "pb",
            "pe",
            "pfcf",
            "ev_ebitda",
            "ps",
            "peg",
            "debt_equity",
            "beta",
            "volatility_1y",
            "range_52w",
        }
    )

    def factor_weight_map(self) -> dict[str, dict[str, float]]:
        return {
            "value": self.value_weights,
            "quality": self.quality_weights,
            "growth": self.growth_weights,
            "momentum": self.momentum_weights,
            "lowvol": self.lowvol_weights,
        }
