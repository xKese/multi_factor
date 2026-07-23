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

    quality_weights: dict[str, float] = field(
        default_factory=lambda: {
            "roe": 0.15,
            "roic": 0.15,
            "roa": 0.10,
            "gross_margin": 0.12,
            "op_margin": 0.12,
            "debt_equity": 0.10,
            "int_coverage": 0.08,
            "current_ratio": 0.08,
            "piotroski": 0.05,
            "altman_z": 0.05,
        }
    )

    growth_weights: dict[str, float] = field(
        default_factory=lambda: {
            "rev_cagr_3y": 0.30,
            "eps_cagr_3y": 0.30,
            "fcf_cagr_3y": 0.20,
            "fwd_eps_growth": 0.20,
        }
    )

    momentum_weights: dict[str, float] = field(
        default_factory=lambda: {
            "ret_1m": 0.15,
            "ret_3m": 0.20,
            "ret_6m": 0.25,
            "ret_12m": 0.20,
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
