"""Anleitung (entspricht Sheet ``Anleitung``)."""

from __future__ import annotations

import dash_bootstrap_components as dbc
from dash import dcc, html, register_page

from app.pages.common import page_title


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

Optional: `SMA (20D)` — kann an beliebiger Stelle ergänzt werden (der Import
erkennt die Spalte am Namen). Aktiviert im Momentum-Monitor die
Kurzfrist-Spalten der Watchlist (SMA-20, Gap 20/50).

Optional: `Est Rev CAGR (1Y)` (bzw. ein Est-/Fwd-/NTM-Revenue-Header mit
„CAGR" oder „Growth" im Namen) — ebenfalls an beliebiger Stelle; wird als
zweiter Forward-Indikator (`Forward Umsatzwachstum`) im Growth-Faktor
gescort. Ohne die Spalte greift die dynamische Neugewichtung.

Fehlt die Spalte `Export Date`, wird das Snapshot-Datum aus dem
Koyfin-Dateinamen gelesen (`koyfin_..._JJJJ.MM.TT_...`), sonst das heutige
Datum verwendet.

4. CSV exportieren und im Tab **Daten-Import** hochladen.

## Scoring-Logik

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback).
- **Dynamische Neugewichtung** bei fehlenden Werten; unter 50 %
  Daten-Abdeckung eines Faktors wird dieser nicht gewertet.
- **Plausibilitätsmaske**: Wachstumsraten über ±300 % (CAGR-Artefakte aus
  negativer Basis) sowie ROE bei negativem Eigenkapital werden ignoriert.
- **Faktor-Score** 0 – 100 → gewichteter Gesamt-Score.
- **Growth** kombiniert historisch (Umsatz/EPS/FCF-CAGR 3J, Umsatzwachstum 1J)
  und forward (EPS- und Umsatz-Schätzungen).
- **Momentum** nutzt das 12-1-Momentum (12M-Return ohne letzten Monat) statt
  des reinen 12M-Returns.
- **Filter**: Piotroski ≥ 5, Altman Z ≥ 1,8, Market Cap ≥ 1 Mrd. Für
  Financials wird das Altman-Kriterium übersprungen (für Banken/Versicherer
  nicht definiert). Piotroski gilt nur bei mindestens 6 von 9 bewertbaren
  Kriterien als aussagekräftig — sonst Filter „-".
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

## Momentum-Monitor (SMA-Signale)

Signale (zustandsbasiert):

- **GOLDEN CROSS**: Kurs > SMA-200 **und** SMA-50 > SMA-200
- **DEATH CROSS**: Kurs < SMA-200 **und** SMA-50 < SMA-200
- sonst: Kurs ≷ SMA-200

Trend-Phasen (wie „lebendig" ist das Signal):

- **Frisch**: |SMA-50 − SMA-200| ≤ 3 % — das Cross liegt nahe
- **Etabliert**: Abstand größer, Trend intakt
- **Ermüdet**: Kurs kreuzt die SMA-50 gegen den Trend oder der
  1M-Return dreht (> 2 % gegenläufig)
- **Neutral**: Kurs und SMA-50 auf verschiedenen Seiten der SMA-200

Events aus der Import-Historie: Jeder CSV-Import speichert die Signal-Zustände
je Aktie. Daraus entstehen **NEU**-Badges (Signalwechsel seit dem letzten
Import) und das Signal-Alter („seit N Tagen"). Die Historie baut sich ab dem
zweiten Import auf.

**12-1-Momentum** = Return 12M − Return 1M (letzter Monat ausgeklammert,
klassische Momentum-Definition); Ranking inkl. Abstand zum 52-Wochen-Hoch.

## M&S Portfolio

Das Portfolio wird als **Koyfin-Watchlist-CSV** auf der Portfolio-Seite
hochgeladen — es genügt eine Ticker-Spalte (Header `Ticker`; Gruppen-Zeilen
wie "Watch" werden ignoriert, Duplikate entfernt). Der Upload wird in der
Datenbank gespeichert und übersteht Neustarts; die Portfolio-Linse auf dem
Momentum-Monitor nutzt automatisch die hochgeladene Liste.

Die Seite überträgt die Modell-Kennzahlen auf den Bestand:

- **Handlungs-Flags** je Position: SELL, Filter nicht bestanden, Death Cross,
  unter SMA-200, ermüdete Trend-Phase, frischer Signalwechsel (NEU) —
  sortiert nach Dringlichkeit
- **Portfolio vs. Universum**: Ø-Score, Faktor-Profil und
  Empfehlungs-Verteilung im Vergleich
- **Positions-Tabelle** mit Scores, Signalen, Phasen und Links zur
  Einzelanalyse

## Factor Timing

Makro-, Bewertungs- und Sentiment-Signale ergeben ein Regime
(GOLDILOCKS / SLOWDOWN / STAGFLATION / HEATING UP), das zusammen mit dem
Factor Momentum die taktische Gewichtung bestimmt.
"""


def layout(**_) -> html.Div:
    return html.Div(
        [
            page_title(
                "Anleitung",
                "Datenquelle, Scoring-Logik und Interpretation der Kennzahlen.",
            ),
            dbc.Card(dbc.CardBody(dcc.Markdown(TEXT))),
        ]
    )


register_page(__name__, path="/anleitung", name="Anleitung", layout=layout)
