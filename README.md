# Multi-Faktor Scoring Model (Dash App)

Eigenständige Python/Dash-Anwendung, die das Multi-Faktor-Scoring-Modell von
Meeder & Seifer eins zu eins abbildet. Dateninput erfolgt ausschließlich über
einen Koyfin-CSV-Upload im Tab **Daten-Import**.

## Funktionsumfang

Die Anwendung ersetzt die 12 Excel-Sheets durch interaktive Seiten:

| Excel-Sheet      | App-Seite                                       |
|------------------|-------------------------------------------------|
| Dashboard        | Übersicht mit KPIs, Top-Ranking                 |
| Einzelanalyse    | Ticker-Detailansicht                            |
| SMA_Signale      | Momentum-Monitor (Trend-Phasen, Cross-Events, 12-1) |
| M&S Portfolio    | Portfolio-Monitor: Koyfin-Watchlist-Upload, Handlungs-Flags, Vergleich zum Universum |
| Factor_Timing    | Taktische Faktor-Allokation mit Makro-Regime    |
| Daten_Import     | CSV-Upload (Koyfin-Export, einziger Input)      |
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
- **Momentum-Monitor**: Trend-Phasen (frisch/etabliert/ermüdet), Signalwechsel
  seit letztem Import (Signal-Historie je Aktie), 12-1-Momentum-Ranking,
  optional SMA-20 aus erweitertem Export

## Installation & Start

```bash
pip install -r requirements.txt
python -m app.main
```

App läuft auf <http://127.0.0.1:8050>. Beim Start ist kein Universum geladen —
das Dashboard zeigt eine Hinweis-Meldung.

Importierte Daten und Einstellungen werden standardmäßig in einer lokalen
SQLite-Datei gespeichert (`data/multifactor.db`, wird automatisch angelegt).
Alternativ kann über die Umgebungsvariable `DATABASE_URL` eine andere
Datenbank gesetzt werden, z. B. PostgreSQL
(`postgresql://user:pass@host/dbname`).

Hinweis: Der PDF-Factsheet-Export (WeasyPrint) benötigt die Systembibliotheken
Pango, Cairo und GDK-Pixbuf. Fehlen sie, läuft die App trotzdem — nur der
PDF-Export schlägt fehl.

## Docker

Vollständig lokaler Betrieb inklusive SQLite-Persistenz und aller
WeasyPrint-Abhängigkeiten:

```bash
docker compose up --build
```

App läuft auf <http://localhost:5000>. Die SQLite-Datenbank liegt im
Docker-Volume `appdata` und überlebt Container-Neustarts.

Ohne Compose:

```bash
docker build -t multi-factor .
docker run -p 5000:5000 -v multi-factor-data:/srv/app/data multi-factor
```

## Daten hochladen

1. Browser auf <http://127.0.0.1:8050/daten-import>
2. Koyfin-CSV-Export (57 Spalten, siehe `app/core/schema.py`; optional
   zusätzlich `SMA (20D)` an beliebiger Position) per Drag & Drop
   oder Klick auswählen
3. Nach erfolgreichem Import werden Dashboard, Einzelanalyse, Momentum-Monitor,
   M&S Portfolio und Perzentil-Hilfe automatisch befüllt
4. Das M&S-Portfolio selbst wird auf der Portfolio-Seite als
   Koyfin-Watchlist-CSV hochgeladen (nur Ticker-Spalte nötig) und in der
   Datenbank gespeichert

### CSV-Format

- Trennzeichen: `;` oder `,` (Auto-Detection)
- Dezimaltrennzeichen: `,` (europäisch)
- Prozent-Werte dürfen als `15,3` oder `0,153` vorliegen (Auto-Normalisierung)
- Spalten-Reihenfolge: siehe `KOYFIN_COLUMNS` in `app/core/schema.py`

## Tests

```bash
python -m tests.test_scoring
```

Smoke-Test gegen `tests/fixtures/koyfin_sample.csv` (10 synthetische Tickers).
