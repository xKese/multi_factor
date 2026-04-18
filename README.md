# Multi-Faktor Scoring Model (Dash App)

Python/Dash-Anwendung, die das Excel-Modell `M&S_Multi-Faktor-Model.xlsx` der
Meeder & Seifer Vermögensverwaltung eins zu eins abbildet.

## Funktionsumfang

Die Anwendung ersetzt die 12 Excel-Sheets durch interaktive Seiten:

| Excel-Sheet      | App-Seite                                       |
|------------------|-------------------------------------------------|
| Dashboard        | Übersicht mit KPIs, Top-Ranking                 |
| Einzelanalyse    | Ticker-Detailansicht                            |
| SMA_Signale      | Signal-Monitor (Golden/Death Cross)             |
| M&S Portfolio    | Firmenportfolio                                 |
| Mein Portfolio   | Persönliches Portfolio                          |
| Factor_Timing    | Taktische Faktor-Allokation mit Makro-Regime    |
| Daten_Import     | CSV-Upload (Koyfin-Export)                      |
| Berechnungen     | automatisch (Scoring-Engine)                    |
| Piotroski        | automatisch (F-Score-Engine)                    |
| Einstellungen    | Editierbare Gewichte/Filter                     |
| Perzentil_Hilfe  | automatisch aus Universum                       |
| Anleitung        | Bedienungsanleitung                             |

## Scoring-Logik

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback)
- **Dynamische Neugewichtung** bei fehlenden Werten
- **5 Faktoren**: Value (25 %), Quality (27 %), Growth (15 %), Momentum (18 %), Low Volatility (15 %)
- **Gesamt-Score 0 – 100** → Klassifikation A / B+ / B / C / D / F
- **Filter**: Piotroski ≥ 5 · Altman Z ≥ 1,8 · Market Cap ≥ 1 Mrd.
- **Empfehlung**: STRONG BUY / BUY / HOLD / SELL
- **Piotroski F-Score** (9 Kriterien, 0 – 9 Punkte)
- **SMA-50/SMA-200-Signal**: Golden Cross, Death Cross, Kurs ≷ SMA-200

## Installation

```bash
pip install -r requirements.txt
python -m app.main
```

App läuft auf <http://127.0.0.1:8050>.

## Koyfin-Export

CSV mit 57 Spalten (siehe `app/core/schema.py`) – Details im Anleitung-Tab.
