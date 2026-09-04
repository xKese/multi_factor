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

**Doppelte Ticker**: Koyfin-Ticker tragen kein Börsensuffix — verschiedene
Aktien können dasselbe Symbol haben (z. B. „SAN" = Sanofi und Banco
Santander). Der Import warnt bei Kollisionen; intern bekommt jede Zeile eine
eindeutige Kennung (`TICKER~namens-slug`, z. B. `SAN~sanofi`), sodass beide
Titel getrennt ansteuerbar sind (Links, Suche, Historie, Agenten- und
Kurs-Mappings). Angezeigt wird weiterhin der Ticker.

4. CSV exportieren und im Tab **Daten-Import** hochladen.

## Scoring v2 & Modellportfolio (primäre Anzeige)

Seit Composite v2 ist das **4-Faktor-Z-Score-Composite** die primäre
Bewertung (umschaltbar über `scoring_version` in den Einstellungen; v1
bleibt als „Scoring v1 (Vergleich)" aufklappbar):

- **Faktoren**: Value (EV/EBITDA, FCF-Rendite, optional EV/EBIT), Quality
  (Gross Profit/Assets, ROIC, Accruals, Leverage), Momentum (12-1
  vol-adjustiert, EPS-Revisionen), Investment (Asset Growth,
  Nettoaktienausgabe). Gewichte 0,30 · 0,30 · 0,25 · 0,15. Financials
  werden auf einem eigenen, reduzierten Indikatorensatz gescort.
- **Neutralisierung**: Z-Scores je Indikator innerhalb `Region × Sektor`
  (Fallback Sektor → Global bei kleinen Gruppen), Winsorisierung
  3 %/97 %, Cap ±3. Keine Median-Imputation, keine stillen Fallbacks —
  jede Ausnahme erscheint als **Diagnose** (Fehler/Warnung/Info) beim
  Daten-Import.
- **Ergebnis je Titel**: `Composite-Score (v2)` (0–100, Perzentil),
  Klasse **A ≥ 90 · B+ ≥ 80 · B ≥ 66,7 · C ≥ 50 · D ≥ 33 · F**, sowie die
  **Zone**: KANDIDAT (Perzentil ≥ 80) · HALTEN (66,7–80, Pufferzone für
  gehaltene Titel) · VERKAUFEN (< 66,7) · FILTER (Universumsfilter nicht
  bestanden). Eine BUY/SELL-Empfehlung gibt es in v2 nicht — die Zone
  ersetzt sie.
- **Universumsfilter** (harte Ausschlüsse, Gründe in `Filter-Gründe`):
  Market Cap ≥ 1 Mrd., Piotroski ≥ 5 (Financials proportional), Altman
  Z ≥ 1,8 (Skip für Financials/Real Estate), Datenabdeckung ≥ 60 %,
  optional Liquidität (`adv_3m`), IPO-Alter (`ipo_date`) und
  Extremverschuldung.
- **Optionale CSV-Zusatzspalten** (Header-Erkennung, Position beliebig):
  `EV/EBIT`, `Net Debt/EBITDA`, `FCF Yield`, `ADV 3M`, `IPO Date` —
  fehlen sie, gelten dokumentierte Fallbacks (mit Info-Diagnose).
- **Modellportfolio** (Seite */modellportfolio*): regelbasierte
  Portfoliokonstruktion auf Basis der Zonen — Zielportfolio von 35
  Titeln, Sektor-/Regions-Bandbreiten (±10 pp), Gewichtung
  `(1+Composite-Z)/Volatilität` mit Floor 2 %/Cap 5 %, Ex-ante-TE-Kontrolle
  (Ziel 4,5–5,5 %, `cTE-Beitrag` je Titel), Trade-Liste mit Aktionen
  BUY/SELL/INCREASE/REDUCE/HOLD/DEFERRED (Turnover-Budget) und
  Override-Register. Der Daten-Import meldet den erkannten
  Rebalance-Modus.
- **Faktor-Timing** wirkt seit v2 nur noch als Monitoring und fließt
  nicht in das Composite ein.

## Scoring-Logik v1 (Vergleichsmodus)

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback).
- **Dynamische Neugewichtung** bei fehlenden Werten; unter 50 %
  Daten-Abdeckung eines Faktors wird dieser nicht gewertet, unter 60 %
  Faktor-Abdeckung gibt es keinen Gesamt-Score (statt eines verzerrten
  Scores aus z. B. nur Momentum + Low-Vol).
- **Plausibilitätsmaske**: mathematisch unmögliche Wachstumsraten
  (< −100 % p. a., Artefakte aus negativer Basis) sowie ROE bei negativem
  Eigenkapital werden ignoriert. Sehr hohe echte Wachstumsraten (> 300 %)
  bleiben gewertet und zählen beim Ranking als „sehr hoch" (gedeckelt).
- **Faktor-Score** 0 – 100 → gewichteter Gesamt-Score.
- **Growth** kombiniert historisch (Umsatz/EPS/FCF-CAGR 3J, Umsatzwachstum 1J)
  und forward (EPS- und Umsatz-Schätzungen).
- **Momentum** nutzt das 12-1-Momentum (12M-Return ohne letzten Monat) statt
  des reinen 12M-Returns.
- **Filter**: Piotroski ≥ 5, Altman Z ≥ 1,8, Market Cap ≥ 1 Mrd. Für
  Financials und Real Estate wird das Altman-Kriterium übersprungen (dort
  nicht definiert bzw. sachfremd kalibriert). Piotroski gilt nur bei
  mindestens 6 von 9 bewertbaren Kriterien als aussagekräftig (Financials:
  4 von 6) — sonst Filter „-".
- **Klassifikation**: A ≥ 80 · B+ ≥ 70 · B ≥ 60 · C ≥ 50 · D ≥ 40 · F < 40.
- **Empfehlung**: STRONG BUY ≥ 80 · BUY ab BUY-Schwelle (Default 70) ·
  HOLD · SELL unter SELL-Schwelle (Default 45); Schwellen editierbar in den
  Einstellungen. Das breite HOLD-Band verhindert Empfehlungs-Flackern.
  Zusätzlich „Empfehlung inkl. Momentum": ein aktives Death Cross bei
  Gesamt-Score < 60 eskaliert HOLD auf „SELL (Death Cross)".
- **Financials-Sonderbehandlung**: Für Banken/Versicherer nicht definierte
  Kennzahlen (EV/EBITDA, P/FCF, Margen, Zinsdeckung, Current Ratio, OCF/NI,
  Debt/Equity, Altman Z) fließen nicht in deren Faktor-Scores und
  Daten-Abdeckung ein.

## Piotroski F-Score (9 Kriterien, 0–9 Punkte)

1. Net Income > 0
2. Operating Cash Flow > 0
3. ROA steigend
4. OCF > Net Income
5. Total Debt sinkend — entfällt für Financials
6. Current Ratio steigend — entfällt für Financials
7. Aktienzahl konstant oder sinkend
8. Gross Margin steigend — entfällt für Financials
9. Asset Turnover steigend

Financials werden auf den verbleibenden 6 Kriterien gescort (0–6 Punkte);
die Filter-Schwelle wird proportional umgerechnet.

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
wie "Watch" werden ignoriert, identische Zeilen entfernt). Bei doppelt
vergebenen Tickern im Universum entscheidet die Namensspalte der Watchlist,
welche Firma gemeint ist; ohne auflösbaren Namen wird die Position als
„mehrdeutig" markiert statt falsch oder doppelt gematcht. Der Upload wird in der
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

Regelbasierte Tilts von max. ±10 % um die strategischen Faktor-Gewichte,
nach Evidenzstärke geordnet:

1. **Faktor-Momentum** (±3 pp): „Aus Universum übernehmen" berechnet je
   Faktor den 6M-Return-Spread Top- minus Bottom-Quintil — objektiv statt
   Schätzeingabe; manuell überschreibbar.
2. **Makro-Regime** (±4 pp): GOLDILOCKS / SLOWDOWN / STAGFLATION /
   HEATING UP aus PMI (Hysterese-Band 49–51), PMI-Trend, OECD CLI, CPI und
   Zinskurve. PMI sinkt + CPI > 3 % → STAGFLATION; inverse Kurve
   (10Y−2Y < 0) stuft GOLDILOCKS auf HEATING UP herab.
3. **Sentiment** (±1–2 pp, symmetrisch): VIX > 25 → Low Vol/Quality,
   VIX < 15 → Momentum; Credit-OAS > 500 bp → Quality statt Value;
   Put/Call > 1,2 (Extremangst, Kontra) → Momentum.

Spread (10Y−2Y) und CPI-YoY lassen sich per Button aus der
Alpha-Vantage-API laden (benötigt `ALPHAVANTAGE_API_KEY`); PMI, CLI und
VIX sind manuelle Eingaben. Die Tabelle zeigt die Tilt-Zerlegung je Faktor
und die aktiven Regeln; jede Regime-Entscheidung wird pro Tag gespeichert
und als Verlauf angezeigt. Der Value-Spread-Badge (Top-Value-P/E vs.
Universums-Median) ist ein reiner Hinweis, kein Tilt.

**Grenzen**: Faktor-Timing hat schwache Out-of-Sample-Evidenz — die
kleinen Tilts sind Absicht; das System unterstützt die Entscheidung, es
ersetzt sie nicht.
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
