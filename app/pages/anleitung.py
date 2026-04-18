"""Anleitung (entspricht Sheet ``Anleitung``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html, register_page


TEXT = """
## Überblick

Diese Anwendung bildet das Excel-Modell **M&S Multi-Faktor-Modell** eins zu eins ab.
Sie arbeitet mit einem globalen Aktien-Universum (~1.300 Werte) und berechnet einen
gewichteten Gesamt-Score aus fünf Faktoren.

## Koyfin-CSV-Export

1. Koyfin → Screener → New Screen
2. Universe wählen (z. B. S&P 500, Stoxx 600, MSCI World)
3. Folgende Spalten **in dieser Reihenfolge** exportieren:

Value: `Price/Book`, `P/E (LTM)`, `P/S (LTM)`, `Price/FCF`, `EV/EBITDA`, `PEG`, `Dividend Yield`
Quality: `ROE %`, `ROA %`, `ROIC`, `Gross Margin %`, `Operating Margin %`, `Debt/Equity`, `Interest Coverage`, `Current Ratio`
Growth: `Revenue 3Y CAGR`, `EPS 3Y CAGR`, `FCF 3Y CAGR`, `EPS Growth (FY1 vs FY0)`, `EPS Estimate Revision 3M`
Momentum: `Return 1M/3M/6M/1Y`
Risk: `Beta`, `Volatility 1Y`, `52W High/Low`
Piotroski-Rohdaten: `Net Income`, `CFO`, `Total Assets`, `Total Debt`, `Current Assets`, `Current Liabilities`, `Shares Out`, `Revenue`, `COGS` (je aktuell und Vorjahr)
Technisch: `SMA-50`, `SMA-200`, `Export Date`

4. CSV exportieren und im Tab **Daten-Import** hochladen.

## Scoring-Logik

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback).
- **Dynamische Neugewichtung** bei fehlenden Werten.
- **Faktor-Score** 0 – 100 → gewichteter Gesamt-Score.
- **Filter**: Piotroski ≥ 5, Altman Z ≥ 1,8, Market Cap ≥ 1 Mrd.
- **Klassifikation**: A ≥ 80 · B+ ≥ 70 · B ≥ 60 · C ≥ 50 · D ≥ 40 · F < 40.
- **Empfehlung**: STRONG BUY / BUY / HOLD / SELL.

## Piotroski F-Score (9 Kriterien, 0–9 Punkte)

1. Net Income > 0
2. Operating Cash Flow > 0
3. ROA steigend
4. OCF > Net Income
5. Total Debt sinkend
6. Current Ratio steigend
7. Aktienzahl konstant oder sinkend
8. Gross Margin steigend
9. Asset Turnover steigend

## SMA-Signale

- **GOLDEN CROSS**: Kurs > SMA-200 **und** SMA-50 > SMA-200
- **DEATH CROSS**: Kurs < SMA-200 **und** SMA-50 < SMA-200
- sonst: Kurs ≷ SMA-200

## Factor Timing

Makro-, Bewertungs- und Sentiment-Signale ergeben ein Regime
(GOLDILOCKS / SLOWDOWN / STAGFLATION / HEATING UP), das zusammen mit dem
Factor Momentum die taktische Gewichtung bestimmt.
"""


def layout(**_) -> html.Div:
    return html.Div(
        [
            html.H2("Anleitung"),
            dbc.Card(dbc.CardBody(dcc.Markdown(TEXT)), className="shadow-sm"),
        ],
        className="p-4",
    )


register_page(__name__, path="/anleitung", name="Anleitung", layout=layout)
