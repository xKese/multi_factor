"""Zentrale Indikator-Gruppen für Anzeige & Export.

Eine einzige Quelle für die Anordnung der Faktor-Kennzahlen, ihre Labels und
Anzeige-Hints. Wird von ``app/pages/einzelanalyse.py`` (UI) und von
``app/core/factsheet_pdf.py`` (PDF-Export) gleichermaßen importiert, damit die
Liste und Reihenfolge konsistent bleiben.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class IndicatorItem:
    label: str
    key: str  # Spaltenname in STATE.scored
    lower_better: bool = False


@dataclass(frozen=True)
class IndicatorGroup:
    name: str
    weight_pct: int  # Anzeige-Gewicht in % (zur Beschriftung)
    color: str  # Hex-Farbe für Bars / Akzente
    items: tuple[IndicatorItem, ...]


# Gewichte und Farben spiegeln das Editorial-Factsheet-Design wider
# (Source: variation-editorial.jsx). Die Indikator-Reihenfolge entspricht der
# bisherigen Anzeige in einzelanalyse.py.
INDICATOR_GROUPS: tuple[IndicatorGroup, ...] = (
    IndicatorGroup(
        name="Value",
        weight_pct=25,
        color="#5b8def",
        items=(
            IndicatorItem("P/B", "pb", lower_better=True),
            IndicatorItem("P/E", "pe", lower_better=True),
            IndicatorItem("P/FCF", "pfcf", lower_better=True),
            IndicatorItem("EV/EBITDA", "ev_ebitda", lower_better=True),
            IndicatorItem("P/S", "ps", lower_better=True),
            IndicatorItem("PEG", "peg", lower_better=True),
            IndicatorItem("Dividendenrendite", "div_yield"),
        ),
    ),
    IndicatorGroup(
        name="Quality",
        weight_pct=27,
        color="#22a06b",
        items=(
            IndicatorItem("ROE", "roe"),
            IndicatorItem("ROIC", "roic"),
            IndicatorItem("ROA", "roa"),
            IndicatorItem("Bruttomarge", "gross_margin"),
            IndicatorItem("Operative Marge", "op_margin"),
            IndicatorItem("Debt/Equity", "debt_equity", lower_better=True),
            IndicatorItem("Zinsdeckung", "int_coverage"),
            IndicatorItem("Current Ratio", "current_ratio"),
            IndicatorItem("Piotroski", "piotroski"),
            IndicatorItem("Altman Z", "altman_z"),
        ),
    ),
    IndicatorGroup(
        name="Growth",
        weight_pct=15,
        color="#d97757",
        items=(
            IndicatorItem("Umsatz CAGR 3J", "rev_cagr_3y"),
            IndicatorItem("EPS CAGR 3J", "eps_cagr_3y"),
            IndicatorItem("FCF CAGR 3J", "fcf_cagr_3y"),
            # Non-Breaking-Hyphen (U+2011) statt "-", damit "Forward
            # EPS‑Wachstum" nicht am Bindestrich auf 3 Zeilen umbricht.
            IndicatorItem("Forward EPS‑Wachstum", "fwd_eps_growth"),
        ),
    ),
    IndicatorGroup(
        name="Momentum",
        weight_pct=18,
        color="#8b5cf6",
        items=(
            IndicatorItem("Return 1M", "ret_1m"),
            IndicatorItem("Return 3M", "ret_3m"),
            IndicatorItem("Return 6M", "ret_6m"),
            IndicatorItem("Return 12M", "ret_12m"),
            # Non-Breaking-Hyphen, damit "EPS‑Revisionen 3M" nur am
            # Leerzeichen wraped, nicht am Bindestrich.
            IndicatorItem("EPS‑Revisionen 3M", "eps_revisions_3m"),
        ),
    ),
    IndicatorGroup(
        name="Low Volatility",
        weight_pct=15,
        color="#0891b2",
        items=(
            IndicatorItem("Beta", "beta", lower_better=True),
            IndicatorItem("Volatilität 1J", "volatility_1y", lower_better=True),
            IndicatorItem("52W Range %", "range_52w", lower_better=True),
        ),
    ),
)


def legacy_pairs() -> Mapping[str, list[tuple[str, str]]]:
    """Rückwärtskompatible (label, key)-Liste je Faktor.

    Die alte Einzelanalyse-Seite konsumiert ``INDICATOR_GROUPS`` als
    ``dict[str, list[tuple[str, str]]]``. Diese Hilfsfunktion liefert genau
    das aus den neuen Datenklassen, damit der Aufrufer nicht angefasst werden
    muss.
    """
    return {g.name: [(it.label, it.key) for it in g.items] for g in INDICATOR_GROUPS}
