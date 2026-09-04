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
| Factor_Timing    | Taktische Faktor-Allokation: Makro-Regime v2 + Faktor-Momentum aus dem Universum + Sentiment (siehe unten) |
| Daten_Import     | CSV-Upload (Koyfin-Export, einziger Input)      |
| Berechnungen     | automatisch (Scoring-Engine)                    |
| Piotroski        | automatisch (F-Score-Engine)                    |
| Einstellungen    | Editierbare Gewichte/Filter                     |
| Perzentil_Hilfe  | automatisch aus Universum                       |
| Anleitung        | Bedienungsanleitung                             |
| —                | Agenten-Analyse: LLM-Tiefenanalyse via TradingAgents (siehe unten) |
| —                | Risiko & Benchmark: Tracking Error, MCTE-Risikobeiträge, Szenarien, Faktor-Schocks (siehe unten) |
| —                | Modellportfolio: Portfoliokonstruktion v2 — Zielportfolio, Trade-Liste, Diagnosen, Overrides (siehe unten) |

## Scoring v2 — Composite (primäres Scoring)

Seit Composite v2 ist das **4-Faktor-Z-Score-Composite** die primäre
Bewertung (`scoring_version = "v2"`, umschaltbar in den Einstellungen;
v1 bleibt Vergleichsmodus und wird bei jedem Import mitberechnet).
Vollständige Methodik in `MODEL_DESCRIPTION.md` (§11/§12) — Kurzfassung:

- **Faktoren** (Region×Sektor-neutrale Z-Scores, Winsorisierung 3 %/97 %,
  Cap ±3, keine Median-Imputation): Value (0,30) · Quality (0,30) ·
  Momentum (0,25) · Investment (0,15). Financials mit eigenem
  Indikatorensatz; Growth und Low Volatility sind keine Faktoren mehr.
- **Ergebnis je Titel**: `composite_z` / `composite_score` (0–100,
  Perzentil), Klasse A ≥ 90 · B+ ≥ 80 · B ≥ 66,7 · C ≥ 50 · D ≥ 33 · F,
  sowie die **Zone** KANDIDAT / HALTEN / VERKAUFEN / FILTER — sie ersetzt
  die v1-Empfehlung.
- **Universumsfilter** (Piotroski, Altman Z, Market Cap, Abdeckung,
  optional Liquidität/IPO/Extremverschuldung) mit protokollierten
  `filter_reasons`; **keine stillen Fallbacks** — jede Ausnahme erzeugt
  eine Diagnose (Fehler/Warnung/Info), sichtbar beim Daten-Import, im
  Dashboard und in der Kopfzeile.
- **Optionale CSV-Zusatzspalten** (Header-Erkennung): `ev_ebit`,
  `net_debt_ebitda`, `fcf_yield`, `adv_3m`, `ipo_date`.
- **Modellportfolio** (`/modellportfolio`, CLI
  `python -m app.tools.model_portfolio build`): regelbasierte Konstruktion
  eines Zielportfolios von 35 Titeln — Sektor-/Regions-Bandbreiten,
  Gewichtung `(1+z)/Vol` mit Floor/Cap, Ex-ante-TE-Kontrolle (Zielband
  4,5–5,5 %), Trade-Liste (BUY/SELL/INCREASE/REDUCE/HOLD/DEFERRED),
  Override-Register und PIT-Historie.

## Scoring-Logik v1 (Vergleichsmodus)

- **Perzentil-Rang** je Indikator (Global / Sektor / Industrie mit Fallback)
- **Dynamische Neugewichtung** bei fehlenden Werten; Faktoren mit weniger als
  50 % Daten-Abdeckung werden nicht gewertet, und ohne mindestens 60 %
  Faktor-Abdeckung gibt es keinen Gesamt-Score
- **Plausibilitätsmasken**: mathematisch unmögliche Wachstumsraten
  (< −100 % p. a.) und ROE bei negativem Eigenkapital werden ignoriert;
  sehr hohe echte Wachstumsraten (> 300 %) zählen als „sehr hoch" (gedeckelt)
- **5 Faktoren**: Value (25 %), Quality (27 %), Growth (15 %), Momentum (18 %), Low Volatility (15 %)
- **Growth** mit historischen (Umsatz/EPS/FCF-CAGR 3J, Umsatzwachstum 1J) und
  Forward-Indikatoren (EPS- und optional Umsatz-Schätzungen)
- **Momentum** auf Basis des 12-1-Momentums (12M-Return ohne letzten Monat)
- **Gesamt-Score 0 – 100** → Klassifikation A / B+ / B / C / D / F
- **Filter**: Piotroski ≥ 5 · Altman Z ≥ 1,8 (übersprungen für Financials
  und Real Estate) · Market Cap ≥ 1 Mrd. Piotroski erfordert mindestens
  6 von 9 bewertbare Kriterien (Financials: 4 von 6), sonst keine
  Filter-Aussage
- **Empfehlung**: STRONG BUY / BUY / HOLD / SELL mit editierbaren Schwellen
  (BUY ab 70, SELL erst unter 45 — das breite HOLD-Band verhindert
  Empfehlungs-Flackern an einer einzelnen Schwelle); zusätzlich
  `Empfehlung inkl. Momentum` (Overlay): ein aktives Death Cross bei
  Gesamt-Score < 60 eskaliert HOLD auf „SELL (Death Cross)", die reine
  Quant-Empfehlung bleibt daneben sichtbar
- **Piotroski F-Score** (9 Kriterien, 0 – 9 Punkte; Financials: 6 Kriterien,
  0 – 6 Punkte — siehe Industriespezifika)
- **SMA-50/SMA-200-Signal**: Golden Cross, Death Cross, Kurs ≷ SMA-200
- **Momentum-Monitor**: Trend-Phasen (frisch/etabliert/ermüdet), Signalwechsel
  seit letztem Import (Signal-Historie je Aktie), 12-1-Momentum-Ranking,
  optional SMA-20 aus erweitertem Export

## Factor Timing (taktische Faktor-Allokation)

Regelbasierte Tilts von max. ±10 % um die strategischen Faktor-Gewichte
(Kern-Logik in `app/core/factor_timing.py`, UI auf `/factor-timing`).
Signal-Hierarchie bewusst nach Evidenzstärke:

1. **Faktor-Momentum** (±3 pp, Top 2 / Bottom 2): automatisch aus dem
   geladenen Universum berechenbar — je Faktor Mittel des 6M-Returns im
   Top-Quintil (nach Faktor-Score) minus Bottom-Quintil („Aus Universum
   übernehmen"), manuell überschreibbar.
2. **Value-Spread**: Median-P/E des Top-Value-Quintils vs. Universum als
   Anzeige-Hinweis (kein eigener Tilt — ohne Historie kein z-Score).
3. **Makro-Regime v2** (±4 pp): GOLDILOCKS / SLOWDOWN / STAGFLATION /
   HEATING UP aus ISM-PMI (mit Hysterese-Band 49–51 gegen Regime-Flackern),
   PMI-Trend, OECD CLI, CPI und Zinskurve (inverse 10Y−2Y-Kurve stuft
   GOLDILOCKS auf HEATING UP herab). Stagflation wird vor Slowdown geprüft
   (PMI sinkt + Inflation > 3 % → STAGFLATION).
4. **Sentiment** (±1–2 pp, symmetrisch): VIX > 25 → Low Vol/Quality;
   VIX < 15 → Momentum; Credit-OAS > 500 bp → Quality statt Value;
   Put/Call als Kontra-Signal (> 1,2 Extremangst → Momentum).

Spread und CPI lassen sich per Button aus der Alpha-Vantage-API übernehmen
(`TREASURY_YIELD` 10y/2y, `CPI` — benötigt `ALPHAVANTAGE_API_KEY`);
PMI/CLI/VIX bleiben manuelle Eingaben. Die Ergebnis-Tabelle zeigt die
**Tilt-Zerlegung** je Faktor (Strategisch | Regime | Momentum | Sentiment |
Taktisch) plus die aktiven Regeln; jede Regime-Entscheidung wird pro Tag
persistiert (`factor_timing_history`) und als Verlauf angezeigt.

Bewusste Grenze: Faktor-Timing hat schwache Out-of-Sample-Evidenz — die
kleinen Tilts sind Absicht, das System ist Entscheidungsunterstützung,
kein Auto-Trade.

## Doppelte Ticker (z. B. „SAN" = Sanofi und Banco Santander)

Koyfin exportiert Ticker ohne Börsensuffix — verschiedene Aktien können
dasselbe Symbol tragen. Die App erkennt Kollisionen beim Import (Warnung im
Import-Bericht) und führt jede Zeile unter einer eindeutigen internen
Kennung (`uid`, `app/core/uid.py`):

- Eindeutiger Ticker → `uid == ticker` (der Normalfall; alle bestehenden
  Links, Mappings und Historien bleiben gültig).
- Kollision → `uid = TICKER~namens-slug`, z. B. `SAN~sanofi` und
  `SAN~bancosantander`. Angezeigt wird weiterhin der Ticker; die uid steckt
  nur in Links, Suche, Dropdown-Werten und Datenbank-Schlüsseln.

Damit sind beide Titel getrennt ansteuerbar (Einzelanalyse, Cmd+K,
Dashboard-Links), bekommen eigene Signal-Historien, eigene
Agenten-Analysen und eigene Ticker-/Alpha-Vantage-Mappings (CLI:
`--map "SAN~sanofi=SAN.PA:EUR"`). Für kollidierende Ticker wird nie
heuristisch gemappt — die Zuordnung läuft über die namens-/regionsgerankte
Symbol-Suche bzw. die Nutzer-Bestätigung.

**Portfolio-Upload:** Enthält die Watchlist einen kollidierenden Ticker,
entscheidet die Namensspalte (exakter oder Prefix-Match, z. B.
„Santander" → „Banco Santander"). Ohne auflösbaren Namen wird die Position
als *mehrdeutig* markiert und nicht gematcht (statt beide Kandidaten
doppelt zu zählen) — Name in der Watchlist ergänzen.

**Grenze:** Historie und Mappings, die vor dem ersten Auftreten einer
Kollision unter dem bloßen Ticker gespeichert wurden, lassen sich
nachträglich keiner der beiden Firmen sicher zuordnen; die kollidierenden
Titel starten unter ihren neuen uids frisch.

## Industriespezifika (Financials & Real Estate)

Für **Financials** (GICS-Sektor, Substring-Match „financ") gelten mehrere
Kennzahlen konzeptionell nicht — Banken haben keine COGS, keine kurzfristige
Bilanzgliederung, Einlagen sind Betriebsmittel statt Risikosignal, und
EBITDA/FCF/OCF sind durch Bilanzbewegungen dominiert. Das Modell behandelt
sie deshalb gesondert:

- **Faktor-Scores**: EV/EBITDA, P/FCF, Brutto-/operative Marge, Zinsdeckung,
  Current Ratio, OCF/NI, Debt/Equity und Altman Z fließen für Financials
  nicht in Value/Quality ein (`FINANCIAL_IRRELEVANT_INDICATORS` in
  `app/core/config.py`) — auch wenn der Export Werte liefert. Value stützt
  sich dann auf P/B, P/E, P/S, PEG und Dividendenrendite, Quality auf
  ROE, ROIC, ROA und Piotroski. Mindest-Abdeckung und `data_coverage`
  bemessen sich an den verbleibenden, anwendbaren Indikatoren.
- **Piotroski**: Financials werden auf 6 statt 9 Kriterien gescort (ohne
  „Verschuldung gesunken", „Current Ratio gestiegen", „Bruttomarge
  gestiegen"); mindestens 4 bewertbare Kriterien nötig. Die Filter-Schwelle
  wird proportional umgerechnet (min. 5 von 9 ≙ 3,33 von 6).
- **Altman Z**: Filterkriterium übersprungen für Financials **und Real
  Estate** — der Z-Score ist auf Industrieunternehmen kalibriert.

**Bekannte Grenze:** Bankspezifische Kennzahlen (CET1-Quote, Cost-Income-
Ratio, Zinsmarge, NPL-Quote, P/TBV) sowie REIT-Kennzahlen (P/FFO,
NAV-Discount) sind im Koyfin-57-Spalten-Export nicht enthalten und können
daher nicht gescort werden. Banken werden über Industrie-Perzentile
(Banken vs. Banken) plus die obigen Ausnahmen fair gerankt, aber nicht auf
regulatorische Kapitalstärke geprüft — dafür ist die Agenten-Tiefenanalyse
oder eine manuelle Prüfung gedacht.

## Installation & Start

```bash
pip install -r requirements.txt
python -m app.main
```

App läuft auf <http://127.0.0.1:8050> (lokaler Dev-Start; unter Docker
stattdessen Port 5000, siehe unten). Beim Start ist kein Universum geladen —
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

Der TradingAgents-Service ist **kein Git-Submodule** dieses Repos, sondern
wird als Geschwister-Verzeichnis erwartet. Beide Repos nebeneinander
auschecken und die LLM-API-Keys in `../TradingAgents/.env` hinterlegen, dann:

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

1. Browser auf <http://127.0.0.1:8050/daten-import> (Docker: Port 5000)
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
- Prozent-Werte müssen als Dezimalanteil vorliegen (`0,153` für 15,3 % —
  Koyfin-Standard). Einzige Ausnahme: die Volatilität liefert Koyfin als
  Prozentzahl, sie wird beim Import automatisch durch 100 geteilt
- Spalten-Reihenfolge: siehe `KOYFIN_COLUMNS` in `app/core/schema.py`

## Risiko & Benchmark (Tracking Error, MCTE, Szenarien)

Eigenständiges Modul (`app/core/av_client|av_store|market_data|risk_*`),
Dash-Seite `/risiko` und CLI. Datenquelle ist die Alpha-Vantage-Premium-API
(Tageskurse adjusted, FX, 10Y-Treasury, WTI); Benchmark ist der iShares MSCI
ACWI ETF (US-Ticker `ACWI`, konfigurierbar) als investierbarer Proxy. Alle
Reihen werden nach EUR umgerechnet — die Ergebnisse bilden die **EUR-Sicht
ohne Currency-Hedging** ab.

### Setup

```bash
export ALPHAVANTAGE_API_KEY=...     # Premium-Key, niemals im Code/Repo

python -m app.tools.risk_report update                  # Kurse/FX/Makro cachen
python -m app.tools.risk_report report --asof 2026-08-20
python -m app.tools.risk_report report --only "COVID,Zinsjahr2022"
python -m app.tools.risk_report report --variante buyhold
```

- `update` lädt inkrementell (nur fehlende Tage; heute schon Abgerufenes wird
  übersprungen) in die App-DB (`av_price_cache`); rückwirkende
  Split-/Dividenden-Adjustierungen werden am Overlap-Tag erkannt und lösen
  einen Full-Refetch aus. `report` rechnet strikt aus dem Cache (keine
  API-Calls) und schreibt `reports/risiko_benchmark_YYYY-MM-DD.md`.
- Exit-Codes: `0` Erfolg, `1` fehlende Daten (kein Portfolio/Cache), `2`
  Argumentfehler — geeignet für einen täglichen Scheduler-Lauf
  (`update` gefolgt von `report`).
- **Portfoliogewichte:** Der Watchlist-Upload auf `/portfolios` darf optional
  eine Gewichtsspalte enthalten (Header `Gewicht`/`Weight`/`Anteil`;
  Dezimalkomma, Prozent- oder Dezimalskala). Ohne Spalte gilt
  Gleichgewichtung (1/N). Beispiel:

  ```csv
  Ticker;Name;Gewicht
  AAPL;Apple;25,0
  SAP;SAP SE;40,0
  ACN;Accenture;35,0
  ```

- **Ticker-Mapping:** Alpha Vantage nutzt eigene Börsen-Suffixe (`SAP.DEX`
  für Xetra, `.LON` für London, `.FRK` für Frankfurt). Die Auflösung läuft
  automatisch (gespeichertes Mapping → Suffix-/Share-Class-Heuristik →
  `SYMBOL_SEARCH`-Validierung, die zugleich die Handelswährung liefert) und
  wird in `av_ticker_mappings` persistiert. Nicht auflösbare Ticker landen
  im Datenqualitätsteil des Reports; manuell zuordnen mit z. B.
  `--map SAP=SAP.DEX:EUR` (Beispielzeilen: `MBG=MBG.DEX:EUR`,
  `BRKB=BRK-B:USD`, `HSBA=HSBA.LON:GBX`).

### Formeln (Kurzfassung)

- Ex-post TE = Std(aktive Tagesrendite) × √252, rollierend 1J/3J und
  Gesamtperiode; aktive Rendite = Portfolio − Benchmark (einfache Renditen
  aus Adjusted Close in EUR); Portfoliovariante `fest` (fixe Gewichte,
  täglich rebalanced, Default) oder `buyhold` (Drift ab Start).
- Information Ratio = annualisierte aktive Rendite / TE; aktives Beta und
  Korrelation per OLS Portfolio vs. Benchmark; Up-/Downside-Capture;
  max. relativer Drawdown des Wertverhältnisses Portfolio/Benchmark.
- Ex-ante TE = √(wᵀΣw · 252) mit Σ = Kovarianz der aktiven Renditen
  (r_i − r_Benchmark, 2 Jahre Tagesdaten), geschätzt mit
  **Ledoit-Wolf-Shrinkage** (`sklearn.covariance.LedoitWolf`); die rohe
  Sample-Kovarianz wird als Robustheits-Check zusätzlich ausgewiesen.
  MCTE_i = (Σw)_i · 252 / TE, CTE_i = w_i · MCTE_i mit Σ CTE_i ≡ TE
  (Unit-Test, Toleranz 1e-10).
- Szenario-Replay: heutige Gewichte durch historische Fenster (GFC,
  Eurokrise, COVID, Zinsjahr 2022, Vol-Schock 2018 — konfigurierbar);
  Gewichte auf verfügbare Titel renormalisiert, Abdeckungsgrad wird
  ausgewiesen, unter 60 % gilt das Szenario als „nicht belastbar“.
- Faktor-Schocks: je Titel OLS der Wochenrenditen (3 Jahre) auf
  Benchmark-Rendite, Δ10Y-Treasury (bp), WTI- und EURUSD-Rendite; Schocks
  (Zinsen +100 bp, Öl +20 %, USD −10 %, Markt −15 %, „Stagflation“) über
  die Betas propagiert; R² < 0,2 → „geringe Erklärungsgüte“.

### Bekannte Limitierungen

- Kovarianz und Betas sind **rückwärtsgerichtet** — Strukturbrüche
  (z. B. Zinsregime) bilden sie erst verzögert ab.
- **Korrelationskonvergenz in Krisen:** In Stressphasen steigen
  Korrelationen sprunghaft; ex-ante TE und Schock-P&L unterschätzen
  Krisenrisiken systematisch.
- Der Benchmark ist ein **ETF-Proxy** (inkl. Kosten/Tracking-Differenz),
  nicht der MSCI-ACWI-Index selbst; Historie erst ab März 2008 — das
  GFC-Fenster ist dadurch nur teilweise abgedeckt.
- Adjusted Close approximiert Total Return (Dividenden reinvestiert am
  Ex-Tag); ACWI-Sektorgewichte sind ein statischer Quartalsstand
  (`Settings.risk_benchmark_sector_weights`, Quelle iShares-Factsheet).
- Einmal gespeicherte Einstellungen: `risk_scenario_windows` und
  `risk_factor_shocks` werden beim Laden **nicht** mit neuen Defaults
  gemergt (nur `dict[str, float]`-Felder werden gemergt) — neue
  Default-Szenarien erreichen Bestandsinstallationen daher nur über ein
  erneutes Speichern der Einstellungen.

## Tests

```bash
python -m tests.test_scoring        # Smoke-Test Scoring
python -m pytest tests -q           # komplette Suite (inkl. Agenten-Client,
                                    # Ticker-Mapping, Agenten-Persistenz,
                                    # Risiko-&-Benchmark-Modul)
```

Smoke-Test gegen `tests/fixtures/koyfin_sample.csv` (10 synthetische Tickers).
