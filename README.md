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
| —                | Agenten-Analyse: LLM-Tiefenanalyse via TradingAgents (siehe unten) |

## Scoring-Logik

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback)
- **Dynamische Neugewichtung** bei fehlenden Werten; Faktoren mit weniger als
  50 % Daten-Abdeckung werden nicht gewertet
- **Plausibilitätsmasken**: Wachstumsraten über ±300 % (CAGR-Artefakte) und
  ROE bei negativem Eigenkapital werden ignoriert
- **5 Faktoren**: Value (25 %), Quality (27 %), Growth (15 %), Momentum (18 %), Low Volatility (15 %)
- **Growth** mit historischen (Umsatz/EPS/FCF-CAGR 3J, Umsatzwachstum 1J) und
  Forward-Indikatoren (EPS- und optional Umsatz-Schätzungen)
- **Momentum** auf Basis des 12-1-Momentums (12M-Return ohne letzten Monat)
- **Gesamt-Score 0 – 100** → Klassifikation A / B+ / B / C / D / F
- **Filter**: Piotroski ≥ 5 · Altman Z ≥ 1,8 (übersprungen für Financials) ·
  Market Cap ≥ 1 Mrd. Piotroski erfordert mindestens 6 von 9 bewertbare
  Kriterien, sonst keine Filter-Aussage
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

## Agenten-Tiefenanalyse (TradingAgents-Integration)

Die App kann pro Titel eine tiefgehende LLM-Agenten-Analyse
([TradingAgents](https://github.com/xKese/TradingAgents)) starten: Markt-,
Sentiment-, News- und Fundamental-Analysten, Bull/Bear-Debatte, Risiko-Runde
und ein finales Rating (Buy/Overweight/Hold/Underweight/Sell). Der
Quant-Score dieser App wird den Agenten dabei als **Vorab-Rating**
mitgegeben (Prior, den die Agenten bestätigen oder widerlegen).

- **Einzelanalyse** → Abschnitt „Agenten-Tiefenanalyse“: Analyse für den
  gewählten Titel starten; Ergebnis wird in der Datenbank gespeichert und
  beim nächsten Besuch wieder angezeigt.
- **Agenten-Analyse** (`/agenten-analyse`): Ad-hoc-Analyse für beliebige
  Ticker — auch außerhalb des Koyfin-Universums — inkl. Symbol-Suche und
  Verlauf aller gespeicherten Analysen.
- **Dashboard**: Spalte „Agenten“ zeigt das jeweils neueste Agenten-Rating.
- **Einstellungen** → Karte „Agenten-Tiefenanalyse“: LLM-Provider, Modelle
  und Analysetiefe.

**Ticker-Zuordnung:** Koyfin-Ticker haben keine Börsen-Endung (z. B. `MBG`),
yfinance/Alpha Vantage brauchen aber das Yahoo-Format (`MBG.F`). US-Titel
(und Titel mit unbekannter Region) starten direkt mit unverändertem Ticker —
Koyfin- und yfinance-Ticker sind bei US-Aktien in der Regel identisch. Nur
bei klar nicht-US-Titeln (Europa/Asien/Kanada) öffnet sich vor der ersten
Analyse ein Bestätigungs-Dialog mit Vorschlägen aus der Symbol-Suche. Über
„Börsen-Ticker ändern …“ in der Einzelanalyse lässt sich die Zuordnung
jederzeit prüfen und korrigieren. Bestätigte Zuordnungen werden gespeichert
(Tabelle `ticker_mappings`).

### Kombinierter Start mit Docker

Beide Repos als Geschwister-Verzeichnisse auschecken und die LLM-API-Keys in
`../TradingAgents/.env` hinterlegen, dann:

```bash
docker compose -f docker-compose.combined.yml up --build
```

Multi-Faktor-UI auf <http://localhost:5000>; der TradingAgents-Service läuft
intern auf `tradingagents-web:8000` (bewusst nicht nach außen veröffentlicht —
die Web-UI hat keine Authentifizierung).

### Lokale Entwicklung ohne Docker

```bash
# Terminal 1 — TradingAgents-Service (API-Keys in der Umgebung/.env)
cd ../TradingAgents && tradingagents serve --port 8000 --no-browser

# Terminal 2 — Multi-Faktor-App
cd multi_factor && TRADINGAGENTS_URL=http://localhost:8000 python -m app.main
```

Ohne erreichbaren Service bleibt die App voll funktionsfähig — nur die
Agenten-Funktionen sind deaktiviert (Hinweis in der UI).

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
python -m tests.test_scoring        # Smoke-Test Scoring
python -m pytest tests -q           # komplette Suite (inkl. Agenten-Client,
                                    # Ticker-Mapping, Agenten-Persistenz)
```

Smoke-Test gegen `tests/fixtures/koyfin_sample.csv` (10 synthetische Tickers).
