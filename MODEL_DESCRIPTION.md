# M&S Multi-Faktor-Modell — Exakte Modell- und Tool-Beschreibung

> **Zweck dieses Dokuments:** Vollständige, präzise Beschreibung des M&S Multi-Faktor-Modells
> (Methodik, Formeln, Gewichte, Schwellenwerte, Architektur) als eigenständiges Kontextdokument,
> z. B. als Input für Claude oder andere LLMs. Alle Werte sind direkt aus dem Code
> (`app/core/`) übernommen und entsprechen den Defaults; die meisten sind über die Seite
> *Einstellungen* (Tabelle `app_settings`) änderbar.

---

## 1. Überblick

Das Tool ist eine deutschsprachige **Python/Dash-Webanwendung**, die das Excel-Modell
`M&S_Multi-Faktor-Model.xlsx` (Meeder & Seifer) **1:1 repliziert** und um mehrere Module
erweitert. Es handelt sich um ein **Aktien-Screening- und Scoring-System**:

- **Input:** ein Koyfin-Screener-CSV-Export (~1.300 globale Aktien, 57 Spalten
  plus optionale Zusatzspalten per Header-Erkennung).
- **Kern (primär, `scoring_version = "v2"`):** ein evidenzbasiertes
  **4-Faktor-Composite** (Value, Quality, Momentum, Investment) auf Basis
  Region×Sektor-neutraler Z-Scores (Abschnitt 11) plus eine **regelbasierte
  Portfoliokonstruktion**, die ein verbindliches Zielportfolio von 35 Titeln
  erzeugt (Abschnitt 12).
- **Vergleichsmodus (v1):** der ursprüngliche **Multi-Faktor-Qualitätsscore
  von 0–100** aus 5 Faktoren (Value, Quality, Growth, Momentum, Low
  Volatility) auf Basis von Perzentil-Rankings innerhalb der GICS-Industrie
  (Abschnitt 3). Beide Versionen werden bei jedem Import berechnet und im
  PIT-Archiv gespeichert; `scoring_version` steuert nur die primäre Anzeige.
- **Output v1:** Klassifikation (A–F), Qualitätsfilter (Piotroski, Altman Z,
  Market Cap) und Empfehlung (STRONG BUY / BUY / HOLD / SELL) je Aktie.
- **Output v2:** `composite_z`/`composite_score`, Klasse v2, Zone
  (KANDIDAT/HALTEN/VERKAUFEN/FILTER), Zielportfolio mit Trade-Liste.
- **Erweiterungsmodule:** Momentum-/SMA-Monitor, Sektor-Momentum, regelbasiertes
  taktisches Faktor-Timing (seit v2 nur Monitoring), Risiko- & Benchmark-Modul
  (Tracking Error), optionale LLM-Tiefenanalyse („TradingAgents").

Tech-Stack: Python 3.11, Dash/Plotly, pandas/numpy, scikit-learn (Ledoit-Wolf),
SQLAlchemy (SQLite oder Postgres), WeasyPrint (PDF-Factsheets), Alpha Vantage API
(nur Risikomodul und Makro-Daten).

---

## 2. Dateninput

### 2.1 Universum: Koyfin-CSV (Pflicht-Input)

Definiert in `app/core/schema.py` (`KOYFIN_COLUMNS`), geladen von
`app/core/data_loader.py` (`load_koyfin_csv`). Das Mapping ist **positional**
(57 Spalten in fester Reihenfolge):

```
ticker, name, sector, industry, region, market_cap, last_price,
pe, pb, ps, pfcf, ev_ebitda, peg, div_yield,
roe, roa, roic, gross_margin, op_margin, debt_equity, int_coverage, current_ratio,
rev_cagr_3y, eps_cagr_3y, fcf_cagr_3y, fwd_eps_growth, eps_revisions_3m,
ret_1m, ret_3m, ret_6m, ret_12m, beta, volatility_1y, high_52w, low_52w, altman_z,
net_income, net_income_prev, ocf, ocf_prev, total_assets, total_assets_prev,
total_debt, total_debt_prev, current_assets, current_liab,
current_assets_prev, current_liab_prev, shares_out, shares_out_prev,
revenue, cogs, revenue_prev, cogs_prev, sma_50, sma_200, export_date
```

- **Optionale Spalten** (per Header-Erkennung, Position beliebig, werden vor dem
  positionalen Mapping extrahiert): `sma_20`, `fwd_rev_growth`.
- Trennzeichen `;` oder `,` wird automatisch erkannt; deutsche Dezimalkommas
  werden geparst.
- **Prozent-Spalten** (`PERCENT_COLUMNS`) liegen im Export bereits als
  Dezimalbrüche vor (0,15 = 15 %). Ausnahme: `volatility_1y` kommt als
  Prozentzahl und wird beim Import durch 100 geteilt.
- **Plausibilitätsvalidierung** (`validate_universe_plausibility`): Der Import wird
  abgelehnt, wenn Spaltenverschiebungen erkennbar sind (Median |`ret_12m`| > 5;
  Verhältnis `last_price`/`sma_200` außerhalb 0,2–5; Median |`beta`| > 5).

### 2.2 Weitere Inputs

- **Portfolio-Watchlist-CSV** (Seite */portfolios*): Spalte `Ticker` erforderlich;
  optionale Gewichtsspalte (`Gewicht`/`Weight`/`Anteil`, % oder Dezimal, wird auf
  Summe 1,0 normiert; ohne Gewichte gilt Gleichgewichtung 1/N).
- **Sektor-ETF-CSV** (10 Spalten, Koyfin-Sektor-Export) für den Legacy-Snapshot des
  Sektor-Momentums.
- **Alpha Vantage API** (`ALPHAVANTAGE_API_KEY`, Premium, Rate-Limit 70 Requests/min):
  `TIME_SERIES_DAILY_ADJUSTED`, `FX_DAILY`, `TREASURY_YIELD`, `WTI`, `SYMBOL_SEARCH` —
  ausschließlich für das Risikomodul und Makro-Reihen des Faktor-Timings.
- **TradingAgents-Service** (`TRADINGAGENTS_URL`, Default `http://localhost:8000`)
  für die LLM-Tiefenanalyse.
- **ENV-Variablen:** `DATABASE_URL` (Default SQLite `data/multifactor.db`),
  `ALPHAVANTAGE_API_KEY`, `TRADINGAGENTS_URL`, `APP_TIMEZONE` (Default Europe/Berlin).

---

## 3. Scoring v1 (Vergleichsmodus) — Perzentil-Scoring

> Scoring v1 bleibt vollständig funktionsfähig und wird bei jedem Import
> mitberechnet; seit Einführung von Composite v2 (Abschnitt 11) ist es der
> **Vergleichsmodus** (`scoring_version = "v1"` bzw. der aufklappbare
> Bereich „Scoring v1 (Vergleich)" in Dashboard/Einzelanalyse).

Implementiert in `app/core/scoring.py`; alle Gewichte/Schwellen in
`app/core/config.py` (Dataclass `Settings`).

### 3.1 Faktoren und strategische Gewichte

| Faktor | Gewicht |
|---|---|
| Value | 25 % |
| Quality | 27 % |
| Growth | 15 % |
| Momentum | 18 % |
| Low Volatility | 15 % |

### 3.2 Indikatorgewichte je Faktor

**Value (7 Indikatoren):**

| Indikator | Gewicht | Invertiert? |
|---|---|---|
| P/E | 0,20 | ja |
| EV/EBITDA | 0,20 | ja |
| P/B | 0,15 | ja |
| P/FCF | 0,15 | ja |
| P/S | 0,10 | ja |
| PEG | 0,10 | ja |
| Dividendenrendite | 0,10 | nein |

**Quality (11 Indikatoren):**

| Indikator | Gewicht | Invertiert? |
|---|---|---|
| ROE | 0,15 | nein |
| ROIC | 0,15 | nein |
| ROA | 0,10 | nein |
| Bruttomarge | 0,12 | nein |
| Operative Marge | 0,12 | nein |
| Debt/Equity | 0,10 | ja |
| Interest Coverage | 0,06 | nein |
| Current Ratio | 0,05 | nein |
| OCF/NI (Cash-Conversion) | 0,05 | nein |
| Piotroski F-Score | 0,05 | nein |
| Altman Z-Score | 0,05 | nein |

**Growth (6 Indikatoren):**

| Indikator | Gewicht |
|---|---|
| Umsatz-CAGR 3J | 0,20 |
| EPS-CAGR 3J | 0,20 |
| Forward-EPS-Wachstum | 0,20 |
| FCF-CAGR 3J | 0,15 |
| Forward-Umsatzwachstum (optional) | 0,15 |
| Umsatzwachstum 1J (abgeleitet) | 0,10 |

**Momentum (aktive Indikatoren):**

| Indikator | Gewicht |
|---|---|
| Return 6M | 0,30 |
| Return 3M | 0,25 |
| 12-1-Momentum (`ret_12m − ret_1m`) | 0,25 |
| EPS-Revisionen 3M | 0,20 |
| Return 1M / Return 12M | je 0,00 (bewusst deaktiviert: Reversal-Effekt bzw. durch 12-1 ersetzt) |

**Low Volatility (3 Indikatoren, alle invertiert):**

| Indikator | Gewicht |
|---|---|
| Beta | 0,40 |
| Volatilität 1J | 0,35 |
| 52-Wochen-Range | 0,25 |

**Invertierte Indikatoren** (`INVERT_LOW_IS_BETTER`, „niedriger = besser", Perzentil = 1 − Perzentil):
`pb, pe, pfcf, ev_ebitda, ps, peg, debt_equity, beta, volatility_1y, range_52w`.

### 3.3 Datenbereinigung vor dem Ranking (`_clean_series`)

- **Negative Multiples → NaN** (`NEGATIVE_IS_INVALID`): `pe, pfcf, peg, ev_ebitda, pb,
  debt_equity` — ein negativer Wert entsteht nur durch negativen Nenner (Verlust,
  negatives EK) und ist nicht „günstig".
- **Wachstumsraten** (`rev_cagr_3y, eps_cagr_3y, fcf_cagr_3y, fwd_eps_growth,
  fwd_rev_growth, rev_growth_1y`): Werte < −100 % → NaN (mathematisch unmöglich,
  Datenartefakt); Werte werden nach oben bei **+300 %** (`GROWTH_CLIP_LIMIT = 3,0`)
  gedeckelt, sodass echtes Hyperwachstum als „sehr hoch" rankt, ohne dass Artefakte
  allein die Spitze bilden.
- **ROE-Maske:** Bei negativem Eigenkapital (Proxy: `debt_equity < 0`) wird ROE auf
  NaN gesetzt (Verlust auf negativem EK ergäbe fälschlich positive ROE).

### 3.4 Abgeleitete Kennzahlen (in `compute_scores`)

| Spalte | Formel |
|---|---|
| `ocf_ni` | `ocf / net_income`, nur wenn `net_income > 0` |
| `rev_growth_1y` | `revenue / revenue_prev − 1`, nur wenn `revenue_prev > 0` |
| `mom_12_1` | `ret_12m − ret_1m` |
| `range_52w` | `(high_52w − low_52w) / low_52w` |
| `sma_200_distance` | `(last_price − sma_200) / sma_200` |
| `sma_50_distance` | `(last_price − sma_50) / sma_50` |
| `sma_gap` | `(sma_50 − sma_200) / sma_200` |
| `dist_52w_high` | `last_price / high_52w − 1` |

### 3.5 Perzentil-Ranking (`_indicator_percentile`)

- Äquivalent zu Excel **PERCENTRANK.INC**: `series.rank(pct=True, method="average")`,
  NaN bleibt NaN.
- **Modus** (`percentile_mode`, Default **„Industrie"**): Ranking innerhalb der
  GICS-Industrie, sofern dort mindestens **5 Titel** (`min_stocks_per_industry`) mit
  Daten vorliegen; sonst Fallback auf **Sektor**, dann **Global**. Modi „Sektor"
  und „Global" sind ebenfalls wählbar.
- Bei invertierten Indikatoren: `pct = 1 − pct`.

### 3.6 Faktor-Score mit dynamischer Neugewichtung (`_factor_score`)

```
faktor_score = Σ(pct_i · w_i · hat_daten_i) / Σ(w_i · hat_daten_i) · 100
```

- Fehlt ein Indikator, verteilen sich die Gewichte automatisch auf die vorhandenen
  (Re-Normierung).
- **Mindest-Abdeckung:** Liegt weniger als **50 %** (`min_factor_coverage = 0,5`) der
  *anwendbaren* Gewichtssumme mit Daten vor, ist der Faktor-Score **NaN** (ein Score
  aus z. B. nur einem Indikator wäre nicht vergleichbar).

### 3.7 Gesamt-Score

```
total_score = Σ(faktor_score_f · W_f · vorhanden_f) / Σ(W_f · vorhanden_f)
```

- Vorhandene Faktoren müssen mindestens **60 %** (`min_total_coverage = 0,6`) der
  Faktor-Gewichtssumme stellen, sonst **NaN**.
- Rundung auf 1 Nachkommastelle; Skala 0–100.
- Zusätzlich `data_coverage` (0–1): mit den Faktorgewichten gewichteter Anteil der
  Indikator-Gewichtssumme, für den valide Daten vorliegen.

### 3.8 Branchen-Sonderlogik: Financials und Real Estate

- **Sektor-Erkennung:** Substring-Match auf den GICS-Sektornamen
  (`"financ"` bzw. `"real estate"`).
- Für **Financials** sind folgende Indikatoren konzeptionell nicht definiert und
  gehen **weder in Faktor-Scores noch in die Abdeckungs-Nenner** ein
  (`FINANCIAL_IRRELEVANT_INDICATORS`):
  `ev_ebitda, pfcf, gross_margin, op_margin, int_coverage, current_ratio, ocf_ni,
  debt_equity, altman_z`.
- **Altman-Z-Filterkriterium** wird für Financials **und** Real Estate übersprungen
  (Z-Score für Banken nicht definiert, für REITs auf sachfremde Bilanzstruktur
  kalibriert).
- **Piotroski:** Financials werden auf 6 statt 9 Kriterien gescort (siehe Abschnitt 4);
  die Filterschwelle wird proportional skaliert.

### 3.9 Klassifikation (`_classify`)

| Gesamt-Score | Klasse |
|---|---|
| ≥ 80 | A – Exzellent |
| ≥ 70 | B+ – Sehr Gut |
| ≥ 60 | B – Gut |
| ≥ 50 | C – Durchschnitt |
| ≥ 40 | D – Unterdurchschnitt |
| < 40 | F – Schwach |

### 3.10 Qualitätsfilter (`_filter_row`)

Alle drei Kriterien müssen erfüllt sein → `JA`, sonst `NEIN`; fehlen benötigte
Daten → `-` (keine Aussage):

1. **Piotroski ≥ 5** (von 9) — für Financials proportional auf die 6er-Skala
   umgerechnet: 5 · 6/9 ≈ 3,33.
2. **Altman Z ≥ 1,8** — übersprungen für Financials und Real Estate.
3. **Market Cap ≥ 1.000** (Mio., Einheit des Exports).

### 3.11 Empfehlung (`_recommendation`) und Overlay

Voraussetzung: Filter bestanden (`JA`); bei `NEIN` lautet die Empfehlung
„Filter nicht bestanden", bei `-` bleibt sie `-`.

| Bedingung | Empfehlung |
|---|---|
| Score ≥ 80 | STRONG BUY |
| Score ≥ 70 (`buy_threshold`) | BUY |
| Score ≥ 45 (`sell_threshold`) | HOLD |
| Score < 45 | SELL |

Das **asymmetrische HOLD-Band (45–70)** wirkt als Hysterese-Ersatz — Titel um eine
Schwelle „flackern" nicht bei jedem Import.

**Empfehlungs-Overlay** (`recommendation_overlay`, separates Feld, die reine
Quant-Empfehlung bleibt sichtbar): Aktives **Death Cross** UND `total_score < 60`
UND Empfehlung HOLD → **„SELL (Death Cross)"**.

---

## 4. Piotroski F-Score (`app/core/piotroski.py`)

9 Kriterien, je 1 Punkt:

| # | Kriterium |
|---|---|
| p1 | Nettogewinn > 0 |
| p2 | Operativer Cashflow > 0 |
| p3 | ROA gestiegen (NI/Total Assets vs. Vorjahr) |
| p4 | OCF > Nettogewinn (Accrual-Qualität) |
| p5 | Gesamtverschuldung gesunken |
| p6 | Current Ratio gestiegen |
| p7 | Aktienzahl nicht gestiegen |
| p8 | Bruttomarge gestiegen |
| p9 | Asset Turnover (Umsatz/Total Assets) gestiegen |

Besonderheiten:

- **Fehlende Daten zählen nicht als verfehlt.** Ein Kriterium ist nur bewertbar, wenn
  seine Inputs vorliegen; nicht bewertbare Kriterien gehen mit 0 Punkten ein
  (konservativ). Sind weniger als **6** Kriterien bewertbar
  (`MIN_VALID_CRITERIA`), ist der F-Score **NaN** → Filter liefert `-` statt `NEIN`.
- **Financials:** p5, p6 und p8 sind sachfremd (Einlagen sind Betriebsmittel, keine
  kurzfristige Bilanzgliederung, keine COGS) → 6-Kriterien-Variante, Maximal-Score 6,
  Mindestanzahl bewertbarer Kriterien 4 (`MIN_VALID_CRITERIA_FINANCIAL`). Die Spalte
  `piotroski_max_criteria` (9 bzw. 6) trägt die Skala je Zeile.

---

## 5. Momentum-/SMA-Monitor (`app/core/momentum.py`, `signal_events.py`)

### 5.1 SMA-Signal (Drei-Bedingungen-Regel, gemäß `TAA Conviction.xlsx`)

| Signal | Bedingung |
|---|---|
| **Golden Cross** | `Kurs > SMA-200` UND `SMA-50 > SMA-200` UND `Kurs > SMA-50` |
| **Death Cross** | `Kurs < SMA-200` UND `SMA-50 < SMA-200` UND `Kurs < SMA-50` |
| Kurs > SMA-200 / Kurs < SMA-200 | sonst, je nach Lage zum SMA-200 |

Die Klassifikation ist **zustandsbasiert** (ein Golden Cross bleibt aktiv, solange die
Bedingungen gelten — unabhängig vom Cross-Zeitpunkt).

### 5.2 Trend-Phasen (`classify_trend_phase`)

7 Zustände: Frisch/Etabliert/Ermüdet bullish, Neutral, Ermüdet/Etabliert/Frisch bearish.

- **Frisch:** |`sma_50` − `sma_200`| / `sma_200` ≤ **3 %** (`FRESH_GAP_THRESHOLD = 0,03`).
- **Ermüdet** (Präzedenz vor Frisch/Etabliert): Kurs kreuzt den SMA-50 gegen den Trend,
  oder der 1M-Return dreht mit > **2 %** (`TIRED_RET_1M = 0,02`) gegen den Trend.
- **Etabliert:** sonst (innerhalb des jeweiligen Regimes).

### 5.3 Signalhistorie

Signalwechsel gegenüber dem vorherigen Import werden aus der Tabelle
`universe_signal_history` abgeleitet (`is_new`, `state_since`, `days_in_state`) —
Grundlage für „NEU"-Badges und den Portfolio-Monitor.

---

## 6. Sektor-Momentum (`app/core/sector_momentum.py`, `sectors.py`)

Aggregiert das gescorte Universum je GICS-Sektor/Industrie:

- Mittelwerte von `total_score`, Returns, 12-1-Momentum, SMA-Abständen;
- **Breadth:** Anteil der Titel über SMA-200 und Anteil aktiver Golden Crosses;
- 12-Wochen-Sparkline aus `sector_score_history`; `delta_score` vs. ~30 Tage altem
  Snapshot;
- **Confidence-Flags** (`low_confidence`): < 3 Titel je Sektor
  (`MIN_TICKERS_PER_SECTOR`), Historie < 2 Snapshots, oder Daten älter als 7 Tage.
- ETF-Zuordnung für die Umsetzung: Sektor-ETFs (IXP, RXI, KXI, IXC, IXG, IXJ, EXI,
  MXI, REET, IXN, JXI) und Industrie-ETFs (u. a. MOO, JETS, IBB) in
  `app/core/sectors.py`.

---

## 7. Faktor-Timing (`app/core/factor_timing.py`)

Regelbasierte **taktische Tilts um die strategischen Faktorgewichte**. Signal-Hierarchie
nach Evidenzstärke: Faktor-Momentum > Value-Spread (nur Anzeige) > Makro-Regime >
Sentiment.

```
taktisch_f = clamp(strategisch_f + regime_tilt_f + momentum_tilt_f + sentiment_tilt_f,
                   0,05, 0,45)   → anschließend Renormierung auf Summe 1,0
```

### 7.1 Faktor-Momentum (±3 pp, `MOMENTUM_TILT = 0,03`)

- Ranking der 5 Faktoren nach Momentum: **Top 2 → Übergewichten (+3 pp)**,
  **Bottom 2 → Untergewichten (−3 pp)**, Mitte neutral.
- Momentum-Proxy aus dem Universum (`factor_momentum_from_universe`): je Faktor
  mittlerer 6M-Return des Top-Quintils (nach Faktor-Score) minus Bottom-Quintil,
  in Prozentpunkten; erfordert ≥ 20 Titel (`MIN_UNIVERSE_FOR_MOMENTUM`).

### 7.2 Makro-Regime (±4 pp, `REGIME_TILT = 0,04`)

Regime-Matrix (Richtung −1/0/+1 · 4 pp):

| Regime | Value | Quality | Growth | Momentum | Low Vol |
|---|---|---|---|---|---|
| GOLDILOCKS | +1 | 0 | +1 | +1 | −1 |
| SLOWDOWN | −1 | +1 | −1 | 0 | +1 |
| STAGFLATION | 0 | +1 | −1 | −1 | +1 |
| HEATING UP | +1 | 0 | 0 | +1 | −1 |

**Regime-Erkennung** (`detect_regime`, deterministische Präzedenz):

1. Wachstum schwach UND CPI > 3 % → **STAGFLATION** (bewusst *vor* SLOWDOWN geprüft).
2. Wachstum schwach → **SLOWDOWN**.
3. Wachstum stark UND CPI > 3 % → **HEATING UP**.
4. Wachstum stark, PMI-Trend ≥ 0, CLI > 0, CPI ≤ 3 % → **GOLDILOCKS**;
   bei inverser Zinskurve (10Y−2Y < 0) heruntergestuft auf **HEATING UP**;
   sonst **HEATING UP** (spätzyklische Mischlage).

„Wachstum schwach" mit **PMI-Hysterese** (49–51): PMI < 49 eindeutig schwach,
PMI > 51 eindeutig stark, dazwischen entscheidet das vorherige Regime; CLI < 0
gilt immer als schwach. Jede Regime-Entscheidung wird täglich in
`factor_timing_history` persistiert.

### 7.3 Sentiment (±1–2 pp, `sentiment_tilts`)

| Regel | Tilt |
|---|---|
| VIX > 25 | Low Vol +2 pp, Quality +1 pp |
| VIX < 15 | Momentum +1 pp, Low Vol −1 pp |
| Credit-OAS > 500 bp | Value −1 pp, Quality +1 pp |
| Put/Call-Ratio > 1,2 (Extremangst, Kontra-Signal) | Momentum +1 pp |
| Put/Call-Ratio < 0,7 (Sorglosigkeit) | Low Vol +1 pp |

### 7.4 Value-Spread (nur Anzeige)

Median-P/E des Top-Value-Quintils ÷ Median-P/E des Universums (< 1 = Value handelt
mit Abschlag). Ohne eigene Historie kein Tilt (kein z-Score möglich).

Makro-Ableitungen aus Alpha Vantage: `spread_from_yields` (10Y−2Y in Prozentpunkten),
`cpi_yoy` (Index[t] / Index[t−12 Monate] − 1).

---

## 8. Risiko- & Benchmark-Modul (`app/core/risk_*.py`)

Benchmark-Proxy: **iShares MSCI ACWI ETF** (`ACWI`, USD-Listing); Kursdaten via
Alpha Vantage, in EUR umgerechnet, Cache in `av_price_cache` (Forward-Fill max.
3 Tage, `FFILL_LIMIT`). Der Cache wird **nur per CLI** befüllt
(`python -m app.tools.risk_report update`); die Seite */risiko* ruft nie die API auf.

### 8.1 Ex-post-Kennzahlen (`risk_metrics.py`)

- Einfache Tagesrenditen aus Adjusted Close in EUR; Annualisierung mit **252**
  Handelstagen.
- Portfolio-Varianten: `fest` (feste Gewichte, täglich rebalanciert, über verfügbare
  Titel renormiert) und `buyhold`.
- **Tracking Error (ex post):** `TE = std(aktive Rendite, ddof=1) · √252`;
  rollierend 1J (252 Tage) / 3J (756 Tage) mit mindestens 80 % Fenster-Abdeckung.
- **Information Ratio:** annualisierte aktive Rendite / TE.
- **Aktives Beta:** `cov(Portfolio, Benchmark) / var(Benchmark)`.
- Up-/Downside-Capture; maximaler relativer Drawdown des Wealth-Ratios; aktive
  Sektorgewichte vs. ACWI-Sektorgewichte (manuell gepflegt in
  `risk_benchmark_sector_weights`).

### 8.2 Ex-ante-TE und Risikobeiträge (`risk_mcte.py`)

- **Ex-ante-TE:** `TE = √(wᵀ Σ w · 252)` mit Σ = **Ledoit-Wolf-geschrumpfte**
  Kovarianzmatrix der aktiven Renditen (sklearn `LedoitWolf`), Fenster **504**
  Handelstage (`COV_WINDOW`).
- **MCTE** je Titel: `MCTE_i = (Σw)_i · 252 / TE`; **CTE** je Titel:
  `CTE_i = w_i · MCTE_i`, mit `Σ CTE_i ≡ TE` (per Unit-Test auf 1e−10 gesichert).
- Rohe Stichproben-Kovarianz wird als Robustheits-Check mit ausgewiesen; Titel mit
  < 60 % Fenster-Abdeckung werden entfernt und offengelegt.

### 8.3 Szenarien (`risk_scenarios.py`)

- **Historische Replays** der aktuellen Gewichte: GFC (2007–2009), Eurokrise (2011),
  COVID (Feb–Mär 2020), Zinsjahr 2022, Vol-Schock 2018; Renormierung auf verfügbare
  Titel, Abdeckung < 60 % (`MIN_COVERAGE`) = „nicht belastbar".
- **Faktorschocks:** je Titel OLS-Regression (3 Jahre wöchentlicher Renditen) auf
  Markt (Benchmark), Zins (Δ10Y in bp), Öl (WTI), USD (EUR/USD); Mindestanzahl
  60 Wochen (`MIN_WEEKLY_OBS`), Mindest-R² 0,2 (`MIN_R2`). Schock-Szenarien:
  Zinsen +100 bp; Öl +20 %; USD −10 %; Markt −15 %; Stagflation
  (Markt −10 %, Zinsen +75 bp, Öl +25 %).

### 8.4 Report (`risk_report.py`)

`compute_risk_report()` orchestriert alles aus dem Cache;
`build_markdown_report()` erzeugt einen deutschen Markdown-Report mit 7 Abschnitten
(Management Summary, TE & Kennzahlen, MCTE je Titel, aktive Sektorallokation,
historische Szenarien, Faktorschocks, Datenqualität) →
`reports/risiko_benchmark_YYYY-MM-DD.md`. CLI-Exit-Codes 0/1/2 für Scheduler.

---

## 9. Weitere Module

- **Portfolio-Monitor** (`app/core/portfolio.py`): Aktions-Flags mit
  Severity-Reihenfolge SELL (0) > FILTER-FAIL (1) > DEATH CROSS (2) >
  UNTER SMA-200 (3) > ERMÜDET (4) > SIGNAL NEU (5).
- **Peers** (`app/core/peers.py`): Vergleichsgruppen aus geschichtetem Pool
  (Industrie → Sektor → Region → Universum); Modi `similar` (euklidische Distanz im
  5-dimensionalen Faktor-Score-Raum) und `top_score`.
- **UID-Logik** (`app/core/uid.py`): kollisionssichere interne Schlüssel — eindeutiger
  Ticker → `uid = ticker`; bei Kollision `TICKER~namens-slug` (z. B. `SAN~sanofi`).
- **TradingAgents-Integration** (`app/core/agents_client.py`): LLM-Tiefenanalyse per
  SSE; `build_factor_context` übergibt den Quant-Score als Prior an die Agenten;
  Ergebnisse in `agent_analyses` archiviert, PDF-Export möglich. Defaults:
  Sprache Deutsch, Temperatur 0,0.
- **PDF-Factsheets** (`app/core/factsheet_pdf.py` + `app/factsheet_template/`):
  redaktionelles A4-Factsheet je Aktie via WeasyPrint (persistenter
  Subprozess-Worker), inkl. automatisch generierter These, Rängen,
  Indikator-Perzentilen und Peers.

---

## 10. Architektur & Bedienung

### 10.1 Struktur

```
main.py                  # Entry: create_app(), 0.0.0.0:5000 (Docker/Replit)
app/main.py              # Dash-App (dev: python -m app.main → 127.0.0.1:8050)
app/core/                # Engine ohne Dash-Abhängigkeiten (siehe oben)
app/pages/               # 12 Dash-Seiten
app/ui/                  # deutsche Formatierer, Labels, Plotly-Theme
app/tools/               # CLIs: risk_report.py, import_taa_history.py
tests/                   # 24 Testdateien + fixtures/ (pytest)
```

### 10.2 Seiten (Route → Zweck)

| Route | Seite |
|---|---|
| `/` | Dashboard (Universum, Rankings) |
| `/einzelanalyse` | Einzelaktien-Analyse (Scores, Perzentile, Peers, Factsheet-PDF) |
| `/agenten-analyse` | LLM-Tiefenanalyse (TradingAgents) |
| `/sma` | Momentum-Monitor (SMA-Signale, Trendphasen) |
| `/sektor-momentum` | Sektor-Momentum |
| `/portfolios` | M&S-Portfolio-Monitor (Watchlist-Upload, Aktions-Flags) |
| `/modellportfolio` | Portfoliokonstruktion v2 (Zielportfolio, Diagnosen, Trade-Liste, Exposures, Override-Register, Historie) |
| `/factor-timing` | Taktisches Faktor-Timing (Monitoring; fließt nicht ins Composite v2) |
| `/risiko` | Risiko & Benchmark (aus Cache) |
| `/daten-import` | Koyfin-CSV-Upload |
| `/einstellungen` | Alle Gewichte/Schwellen (persistiert in `app_settings`) |
| `/perzentil-hilfe`, `/anleitung` | Hilfe/Anleitung |

### 10.3 Persistenz

SQLAlchemy (SQLite Default, Postgres via `DATABASE_URL`), Tabellen u. a.:
`koyfin_universe`, `koyfin_universe_history`, `koyfin_meta`,
`universe_signal_history`, `sector_momentum_snapshots`,
`sector_score_history`, `ms_portfolio`, `app_settings`,
`factor_timing_inputs`, `factor_timing_history`, `agent_analyses`,
`ticker_mappings`, `av_price_cache`, `av_symbol_meta`, `av_ticker_mappings`,
sowie für Composite v2/Portfoliokonstruktion: `model_portfolio`,
`model_portfolio_meta`, `override_register`,
`risk_benchmark_region_weights` (Abschnitt 12.8).

**PIT-Archiv (Punkt-in-Zeit):** Jeder CSV-Import archiviert das gescorte
Universum (Rohkennzahlen + berechnete Scores) zusätzlich in
`koyfin_universe_history` — Spalten wie `koyfin_universe` plus
`snapshot_date` (DATE, Export-Datum des CSV, Fallback Importdatum) und
`imported_at` (TIMESTAMP), Unique-Index auf `(snapshot_date, uid)`.
Ein Re-Import mit gleichem `snapshot_date` ersetzt nur diesen Snapshot
(UPSERT auf Snapshot-Ebene); ältere Snapshots werden nie gelöscht oder
überschrieben. Die Tabelle entsteht beim ersten Import (Bestands-DBs
werden ohne Datenverlust erweitert); neue Spalten späterer Exporte werden
per `ALTER TABLE` nachgerüstet (SQLite und Postgres). Seit Composite v2
enthält jeder Snapshot zusätzlich alle v2-Spalten: `z_*`, `cov_*`,
`composite_raw/z/pct/score`, `classification_v2`, `zone_v2`,
`filter_pass`, `filter_reasons` (JSON-Text), `neut_level_*`,
`data_coverage_v2`, `trend_warning`, `fcf_yield_source` u. a.;
Listen-Spalten werden vor dem Schreiben JSON-serialisiert. Die unveränderte
Roh-CSV wird unter `data/archive/koyfin_<snapshot_date>.csv` abgelegt
(gleicher Dateiname wird überschrieben; Pfad via `KOYFIN_ARCHIVE_DIR`
umlenkbar). Zugriff für Auswertungen:
`persistence.load_universe_snapshot(snapshot_date)` und
`persistence.list_snapshots()`.

### 10.4 Typischer Workflow

1. Koyfin-CSV auf */daten-import* hochladen → Universum wird gespeichert, gescort,
   Signal- und Sektor-Historie fortgeschrieben.
2. Dashboard, Einzelanalyse, Momentum-Monitor, Sektor-Momentum und Faktor-Timing
   speisen sich automatisch aus dem gescorten Universum.
3. Portfolio-Watchlist separat auf */portfolios* hochladen.
4. Risikodaten per CLI aktualisieren:
   `python -m app.tools.risk_report update` und
   `python -m app.tools.risk_report report [--asof … --variante fest|buyhold]`.
5. Zielportfolio auf */modellportfolio* berechnen (der Import meldet den
   erkannten Rebalance-Modus) oder per CLI:
   `python -m app.tools.model_portfolio build [--mode …] [--dry-run]`.

### 10.5 Start & Tests

```bash
pip install -r requirements.txt
python -m app.main                     # dev, http://127.0.0.1:8050
python main.py                         # http://0.0.0.0:5000
docker compose up --build              # http://localhost:5000
python -m pytest tests -q              # Testsuite
```

---

## 11. Composite v2 (primäres Scoring)

Implementiert in `app/core/scoring_v2.py` (+ `app/core/universe_filter.py`,
`app/core/diagnostics.py`). Vier Faktoren, Z-Score-basiert,
Region×Sektor-neutral. Alle Parameter sind `Settings`-Felder
(`app_settings`), Verfahrenskonstanten (`V2_CLEAN_BOUNDS`,
`V2_NEGATIVE_IS_INVALID`, `PC_TE_STEP = 0,005`, `PC_TE_MAX_ITER = 40`,
`PC_CAPFLOOR_MAX_ITER = 50`) liegen in `app/core/config.py`.
Leitprinzip: **keine stillen Fallbacks** — jede nicht anwendbare Regel
erzeugt einen Eintrag in der Diagnoseliste (`Diagnostic` mit Schweregrad
Fehler/Warnung/Info), sichtbar in UI und Report. Determinismus: gleiche
Eingaben und Settings ergeben identische Ausgaben (Tie-Break `uid`).

### 11.1 Optionale Zusatzspalten (Header-Erkennung, Position beliebig)

| Spalte | Bedeutung | Verwendung, falls vorhanden |
|---|---|---|
| `ev_ebit` | EV/EBIT | dritter Value-Indikator (Nicht-Financials); fehlt die Spalte, besteht Value aus 2 Indikatoren |
| `net_debt_ebitda` | Nettoverschuldung/EBITDA | ersetzt den Leverage-Proxy in Quality |
| `fcf_yield` | FCF/EV | primärer FCF-Value-Indikator; je Titel Fallback `1/pfcf` (FCF/Marktkap., `fcf_yield_source` ∈ {"ev","mcap"}, Anteil in der Diagnose) |
| `adv_3m` | Ø Tagesumsatz 3M (Mio EUR) | Liquiditätsfilter |
| `ipo_date` | Erstnotiz (ISO) | IPO-Filter |

Fehlende optionale Spalten → dokumentierter Fallback + Info-Diagnose je Import.

### 11.2 Abgeleitete Kennzahlen (`derive_v2_indicators`, Spec 1.3)

| Spalte | Formel | Gültigkeit (sonst NaN) |
|---|---|---|
| `gp_ta` | `(revenue − cogs) / total_assets` | `total_assets > 0`, revenue und cogs vorhanden |
| `accruals` | `(net_income − ocf) / total_assets` | `total_assets > 0` |
| `ebit_proxy` | `revenue · op_margin` | beide vorhanden |
| `debt_ebit` | `total_debt / ebit_proxy` | `ebit_proxy > 0`; bei `total_debt ≤ 0` → 0 |
| `fcf_yield_calc` | `1 / pfcf` | `pfcf > 0`; nur wo `fcf_yield` (FCF/EV) fehlt oder je Titel NaN ist |
| `asset_growth` | `total_assets / total_assets_prev − 1` | `total_assets_prev > 0` |
| `share_issuance` | `shares_out / shares_out_prev − 1` | `shares_out_prev > 0` |
| `mom_12_1` | `ret_12m − ret_1m` | — |
| `mom_12_1_adj` | `mom_12_1 / volatility_1y` | `volatility_1y ≥ 0,05` (`v2_min_volatility`); fehlende Vola → Fallback `mom_12_1` (Info) |
| `is_financial` / `is_real_estate` | Substring „financ" / „real estate" auf `sector` | — |

Bereinigung vor der Standardisierung (nur auf internen Kopien, v1-Spalten
bleiben unangetastet): negative Multiples → NaN für `pe, pb, pfcf,
ev_ebitda, ev_ebit`; `fcf_yield` außerhalb [−0,5, 0,5] → NaN (negativer FCF
bleibt gültig); `net_debt_ebitda` < −20 oder > 50 → NaN (Nettocash gültig);
`debt_ebit` > 50 → NaN; `asset_growth`/`share_issuance` außerhalb
[−0,9, 3,0] → NaN; `accruals` außerhalb [−1, 1] → NaN; `gp_ta` außerhalb
[−1, 3] → NaN; `roic` außerhalb [−1, 2] → NaN; `eps_revisions_3m`
außerhalb [−1, 1] → NaN; negative `debt_equity` → NaN (v1-Konvention).

### 11.3 Faktor-Definitionen (Indikatoren gleichgewichtet; Richtung −1 = niedrig ist gut)

**Nicht-Financials:**

| Faktor | Indikatoren (Richtung) |
|---|---|
| Value | `ev_ebitda` (−1), `fcf_yield` (+1), `ev_ebit` (−1, optional) |
| Quality | `gp_ta` (+1), `roic` (+1), `accruals` (−1), Leverage (−1): `net_debt_ebitda` → Fallback `debt_ebit` → `debt_equity` (Spaltenebene, Diagnose-Info) |
| Momentum | `mom_12_1_adj` (+1), `eps_revisions_3m` (+1) |
| Investment | `asset_growth` (−1), `share_issuance` (−1) |

**Financials** (`is_financial`): Value `pb` (−1), `pe` (−1); Quality `roe`
(+1, nur bei `debt_equity ≥ 0`), `accruals` (−1); Momentum wie oben;
Investment `share_issuance` (−1). Alle Nicht-Fin-Indikatoren entfallen
vollständig — auch im Abdeckungs-Nenner.

**Real Estate:** wie Nicht-Financials, aber ohne `accruals`.

**Strategische Faktorgewichte** (`v2_weight_*`, Validierung Summe
1,0 ± 0,001, sonst Import-Fehler): Value 0,30 · Quality 0,30 ·
Momentum 0,25 · Investment 0,15. Low Volatility ist kein Faktor mehr
(wirkt nur in der Gewichtung, 12.3); Growth entfällt ersatzlos; Piotroski
und Altman wandern vollständig in die Filter (12.1).

### 11.4 Standardisierung und Aggregation

1. **Neutralisierungsgruppen** (`assign_neutralization_group`, je
   Indikator): Primärgruppe `region × sector`; hat sie weniger als
   `v2_min_group_size = 20` gültige Werte → Fallback `sector` (global) →
   `global`. Ebene je Titel/Indikator in `neut_level_<indikator>`
   (`region_sector` | `sector` | `global`).
2. **Winsorisierung + Z-Score** (`zscore_within_group`, je Indikator und
   Gruppe): Clip auf die 3 %/97 %-Quantile (`v2_winsor_lower/upper`),
   `z = (x_w − mean) / std(ddof=1)`, Clip ±3 (`v2_zscore_cap`), ·Richtung.
   `std == 0` oder < 5 gültige Werte (`v2_min_group_valid`) → `z = 0` +
   Diagnose. NaN bleibt NaN — **keine Median-Imputation**.
3. **Faktor-Score** (`factor_zscore`): Mittel der gültigen Indikator-Z.
   Mindestabdeckung (`v2_min_valid_nonfin` / `v2_min_valid_financial`):
   Nicht-Fin Value 2 (bei nur 2 Indikatoren: 1), Quality 2, Momentum 1,
   Investment 1; Financials je 1. Darunter → Faktor NaN. Zusätzlich
   `cov_<faktor>` (Anteil gültiger Indikatoren).
4. **Composite** (`composite_zscore`):
   `composite_raw = Σ(z_f · W_f · vorhanden_f) / Σ(W_f · vorhanden_f)`;
   NaN, wenn die Gewichte vorhandener Faktoren < 0,70
   (`v2_min_factor_weight`) oder weder Value noch Quality vorhanden.
   Danach zweite globale Standardisierung (Winsor 1 %/99 %, Z-Score, Cap
   ±3) → `composite_z`; `composite_pct = rank(pct, average)`;
   `composite_score = round(composite_pct · 100, 1)`.
5. **Klassifikation v2** (nur Anzeige): A ≥ 0,90 · B+ ≥ 0,80 · B ≥ 0,667 ·
   C ≥ 0,50 · D ≥ 0,33 · F < 0,33. Die v1-Empfehlung wird für v2 nicht
   übernommen — stattdessen `zone_v2`.
6. **Datenabdeckung v2:** `data_coverage_v2` = faktorgewichtetes Mittel der
   `cov_*` (fehlende Faktoren zählen 0).

### 11.5 Zonen (Spec 5.2)

`zone_v2`: `FILTER` (nicht eligible) · `KANDIDAT` (eligible, `composite_pct
≥ pc_entry_pct = 0,80`) · `HALTEN` (`pc_exit_pct = 0,667 ≤ pct <
pc_entry_pct`) · `VERKAUFEN` (`pct < pc_exit_pct`). Gehaltene Titel:
HALTEN bleibt im Portfolio (Pufferzone), VERKAUFEN/FILTER wird verkauft.

---

## 12. Portfoliokonstruktion (Modellportfolio)

Implementiert in `app/core/portfolio_construction.py`; UI auf
`/modellportfolio`, CLI `python -m app.tools.model_portfolio`.

### 12.1 Universumsfilter (harte Ausschlüsse, `apply_universe_filters`)

Alle verletzten Bedingungen werden in `filter_reasons` protokolliert
(kein Abbruch bei der ersten); `filter_pass` = keine Verletzung:

| # | Filter | Regel (Setting) | Fehlende Daten |
|---|---|---|---|
| 1 | Market Cap | ≥ 1.000 Mio EUR (`filter_min_market_cap`) | nicht eligible (`market_cap_na`) |
| 2 | Piotroski | ≥ 5 von 9; Financials proportional ≥ 3,33 von 6 (`filter_min_piotroski`) | nicht eligible (`piotroski_na`) |
| 3 | Altman Z | ≥ 1,8 (`filter_min_altman`); Skip für Financials und Real Estate | nicht eligible außer bei Skip |
| 4 | Liquidität | `adv_3m ≥ 2,0` Mio EUR (`filter_min_adv`); nur wenn Spalte vorhanden | Spalte fehlt → übersprungen (Info) |
| 5 | Abdeckung | `data_coverage_v2 ≥ 0,6` (`filter_min_coverage`) und `composite_z` nicht NaN | — |
| 6 | IPO | `ipo_date` ≥ 365 Tage vor Snapshot (`filter_min_listing_days`); nur wenn Spalte vorhanden | Spalte fehlt → übersprungen |
| 7 | Extremverschuldung | Nicht-Fin: `debt_equity > 3,0` UND `int_coverage < 2,0` (`filter_max_de`/`filter_min_icr`) | fehlende Werte → greift nicht |
| 8 | Override | aktiver Override `direction = "exclude"` | — |

### 12.2 Selektion (`select_portfolio`, Spec 5.4)

Parameter: `pc_target_n = 35`, `pc_min_n = 25`, `pc_max_n = 40`,
`pc_fill_pct = 0,70`, `pc_sector_band`/`pc_region_band = 0,10` (± pp),
`pc_max_per_sector = 8`. Algorithmus: (1) `retained` = gehaltene Titel in
KANDIDAT/HALTEN ∪ Include-Overrides, Rest = Verkäufe; (2) Kandidaten =
KANDIDAT-Zone, `composite_z` absteigend, Tie-Break `uid`; (3–4) Aufnahme
nur, wenn mit den vorläufigen Gewichten (12.3, ohne TE) Sektor- und
Regions-Band sowie `pc_max_per_sector` eingehalten werden; (5) unter
`pc_min_n` Notfüllzone `pc_fill_pct ≤ pct < pc_entry_pct` mit Warnung
„Notfüllung"; (6) über `pc_max_n` (nur durch Includes) → Warnung, kein
automatisches Entfernen; (7) unter `pc_min_n` → Fehler-Diagnose,
Zielportfolio trotzdem ausgegeben. Das Modell **verkauft nie wegen einer
Bandbreite** (Pufferzone hat Vorrang, Verletzung nur als Warnung).

Benchmark-Gewichte: Sektoren aus `Settings.risk_benchmark_sector_weights`
mit Stand `risk_benchmark_sector_weights_asof`; Regionen aus der Tabelle
`risk_benchmark_region_weights` (`region`, `weight`, `asof`;
Regionsnamen exakt wie Koyfin-Spalte `region`, unbekannte Regionen
Benchmark 0 + Diagnose). Quelle fehlt oder älter als 120 Tage
(`pc_benchmark_max_age_days`) → Band ausgesetzt + Warnung.

### 12.3 Gewichtung (`compute_weights`, Spec 6.1–6.2)

`tilt = 1 + clip(composite_z, 0, 3)`; `vol = clip(volatility_1y,
pc_vol_floor = 0,10, pc_vol_cap = 0,60)` (fehlend → Portfolio-Median +
Info); `w_raw = tilt / vol`, normiert. Dann iterativ (≤ 50 Iterationen):
Clip auf [`pc_weight_floor = 0,02`, `pc_weight_cap = 0,05`],
Überschuss/Defizit proportional auf nicht gebundene Titel, bis Σ = 1 ±
1e−9. `floor·N > 1` oder `cap·N < 1` → Fehler-Diagnose.

### 12.4 Ex-ante-TE-Kontrolle (`apply_te_constraint`, Spec 6.3)

Nutzt `risk_mcte.compute_mcte` (Ledoit-Wolf, 504 Tage, ACWI in EUR, nur
Kurs-Cache). Kursabdeckung < `pc_te_min_coverage = 0,60` des
Portfoliogewichts → Schritt übersprungen, Warnung „TE nicht prüfbar".
Solange `TE > pc_te_max = 0,060` oder `max(CTE_i/TE) > pc_max_cte_share =
0,15` (max. 40 Iterationen): Titel mit höchstem CTE um 0,005 reduzieren
(nicht unter floor), gleichmäßig auf die drei Titel mit niedrigstem CTE
verteilen (nicht über cap); Weight-Overrides ausgenommen. Nach 40
Iterationen unerfüllt → letzter Zustand + **Fehler-Diagnose**
„TE-Restriktion nicht erfüllbar" (Pflichtpunkt Investmentkomitee, kein
Abbruch). `TE < pc_te_target_low = 0,045` → Info. Zielband
0,045–0,055 (`pc_te_target_low/high`).

### 12.5 Rebalancing-Kalender und Turnover (`detect_rebalance_mode`, `build_trade_list`, Spec 7)

Modi: `full` (erster Import nach dem letzten Handelstag der Monate
`pc_rebalance_months = [3, 9]`; Näherung: letzter Werktag Mo–Fr, kein
Feiertagskalender), `interim` (analog `pc_interim_months = [6, 12]`; nur
Verkäufe und deren Ersatz aus KANDIDAT, bestehende Gewichte bleiben bis
auf Renormierung — das freiwerdende Gewicht geht gleichmäßig an die
Ersatztitel), `monitor` (kein Zielportfolio-Update; Filter-Fails
gehaltener Titel als „Sofortmaßnahme-Vorschlag"-Diagnose). Modus wird aus
`snapshot_date` und der letzten `model_portfolio`-Version abgeleitet, auf
`/modellportfolio` manuell überschreibbar (protokolliert); ohne
Vorversion `full`; sind Full- und Interim-Trigger überfällig, hat `full`
Vorrang.

Turnover-Budget (einseitig = `0,5 · Σ|Δw|`): full 0,20, interim 0,10
(`pc_turnover_budget_*`). Bei Überschreitung werden Trades in dieser
Priorität behalten, alle weiteren als `VERSCHOBEN` gestrichen (im Budget
verbleibende günstigere Trades werden weiter befüllt): (1) Verkäufe wegen
FILTER (Pflicht), (2) Verkäufe wegen Override-Exclude (Pflicht), (3)
Verkäufe VERKAUFEN aufsteigend nach `composite_z`, (4) Käufe KANDIDAT
absteigend nach `composite_z`, (5) Gewichtsanpassungen absteigend nach
`|Δw|`; danach Renormierung auf die umgesetzten Positionen. Aktionen:
KAUF/VERKAUF/AUFSTOCKEN/REDUZIEREN/HALTEN/VERSCHOBEN mit `reason`;
`|Δw| < pc_min_trade_size = 0,005` ist kein Trade. `build_trade_list`
besitzt einen (derzeit ignorierten) Parameter `tax_lots` als
Schnittstelle für spätere steuerliche Optimierung.

### 12.6 Override-Register (Tabelle `override_register`, Spec 8)

Spalten: `id, uid, direction (exclude|include|weight), target_weight
(nur weight), reason (Pflicht, ≥ 20 Zeichen), owner (Pflicht),
created_at, expires_at (Pflicht, ≤ created_at + 180 Tage), status
(active|expired|closed), closed_at/closed_by/close_note`. Validierung im
Writer (`save_override`) UND als DDL-Constraints. Abgelaufene Overrides
werden bei jedem Import auf `expired` gesetzt und nicht mehr angewendet
(Diagnose „Override abgelaufen — erneuern oder schließen"). `exclude` →
Filter 8; `include` → Titel wird in `retained` aufgenommen; `weight` →
Gewicht fixiert (nimmt nicht an Cap/Floor und TE teil), Rest renormiert
auf `1 − Σ Override-Gewichte`. Historisiert werden **beide** Gewichte:
`weight_model` (ohne) und `weight_effective` (mit Overrides).

### 12.7 Zurückstufung Faktor-Timing und SMA-Overlay (Spec 9)

`factor_timing_mode` (`monitor` Default | `active`): im Monitor-Modus
werden die taktischen Gewichte weiterhin berechnet und auf
`/factor-timing` angezeigt, fließen aber **nicht** in `composite_z` ein.
Im Active-Modus (nur Backtests) ersetzen sie die strategischen Gewichte;
Mapping (`map_tactical_to_v2`): Value/Quality/Momentum übernommen,
Investment behält sein strategisches Gewicht (kein v1-Pendant),
Renormierung auf 1,0, Info-Diagnose. Das v1-Feld `recommendation_overlay`
wird für v2 nicht übernommen; stattdessen `trend_warning` (bool, aktives
Death Cross) als reine Information in Trade-Liste und Diagnose — ein
Death Cross löst **keinen** Verkauf aus. Der Portfolio-Monitor erhält die
Zonen als zusätzliche Flags (`V2: FILTER`, `V2: VERKAUFEN`), die
v1-Severity-Logik bleibt unverändert.

### 12.8 Persistenz und CLI

Tabellen (12.6 plus): `model_portfolio` (je `snapshot_date`/`uid`:
`composite_z, composite_pct, zone_v2, weight_model, weight_effective,
cte, action, reason, rebalance_mode, override_id`; Unique
(`snapshot_date`, `uid`), Snapshot-UPSERT wie im PIT-Archiv),
`model_portfolio_meta` (`rebalance_mode, n_titles, te_ex_ante,
te_coverage, turnover_oneway, n_trades, n_deferred, settings_hash,
diagnostics` JSON), `risk_benchmark_region_weights`. `settings_hash` =
SHA-256 über die JSON-serialisierten, sortierten v2-/pc-/filter-Settings.

CLI: `python -m app.tools.indicator_correlation [--snapshot]`
(Spearman-Matrix aller `z_*`- und v1-Perzentil-Indikatoren, getrennt
Nicht-Fin/Financials; Average-Linkage-Clustering auf `1 − |ρ|`, Schwelle
|ρ| ≥ 0,8; Abdeckung; Report `reports/indikator_korrelation_<datum>.md` +
CSV — vor Produktivsetzung von v2 einmal auszuführen) und
`python -m app.tools.model_portfolio build [--snapshot] [--mode
full|interim|monitor] [--dry-run]` (Report
`reports/modellportfolio_<datum>.md`; Exit 0 ohne Fehler-Diagnosen, 1 bei
Warnungen, 2 bei Fehlern) bzw. `… compare --v1 --v2` (Spearman v1/v2,
Rangänderungen > 30 Perzentilpunkte, Sektorverteilung der Top-35).

---

## 13. Bekannte Grenzen und bewusste Annahmen

- **Ein-Zeitpunkt-Scoring, aber PIT-Archiv:** Das Scoring basiert auf dem
  jeweils letzten CSV-Export. Seit Einführung des PIT-Archivs (Abschnitt 10.3)
  wird jedoch jeder Import als Punkt-in-Zeit-Snapshot in
  `koyfin_universe_history` archiviert (inkl. Roh-CSV unter `data/archive/`) —
  Backtests der Scores werden damit ab dem ersten archivierten Import möglich;
  für Zeiträume davor existiert weiterhin keine Historie.
- **Faktor-Momentum ist ein Querschnitts-Proxy** (Quintils-Spread der 6M-Returns),
  keine echte Faktor-Return-Zeitreihe.
- **Value-Spread** liefert bewusst keinen Tilt (keine Historie → kein z-Score).
- **ACWI-Sektorgewichte** werden manuell (quartalsweise) aus dem iShares-Factsheet
  gepflegt.
- **Faktorschocks:** OLS-Intercept wird gefittet, aber nicht in die Schock-Projektion
  propagiert; Titel unterhalb `MIN_R2 = 0,2` bzw. mit < 60 Wochen Daten fallen aus
  der Schock-Rechnung und werden ausgewiesen.
- **Risiko-Cache nur per CLI** — die UI zeigt ausschließlich zwischengespeicherte
  Daten (bewusste Trennung von API-Last und Bedienung).
- **Perzentil-Scores sind relativ zum geladenen Universum** — ein anderes Universum
  ergibt andere Scores; Vergleichbarkeit setzt konsistente Exporte voraus.
- **Composite v2 — Werktags-Näherung:** Der Rebalance-Kalender approximiert den
  „letzten Handelstag" eines Monats als letzten Werktag (Mo–Fr), ohne
  Feiertagskalender.
- **Composite v2 — FCF-Yield-Fallback:** Fehlt die optionale Spalte `fcf_yield`
  (FCF/EV), wird je Titel `1/pfcf` (FCF/Marktkapitalisierung) verwendet —
  konzeptionell abweichend; der Ursprung wird je Titel (`fcf_yield_source`)
  und als Anteil in der Diagnose ausgewiesen. Das EV-Vorzeichen ist aus der
  gelieferten Ratio nicht beobachtbar; durchsetzbar ist nur das Band
  [−0,5, 0,5].
- **Faktor-Timing-Active-Mapping:** Die v1-Faktoren decken v2 nicht 1:1 —
  im Modus `active` werden Value/Quality/Momentum taktisch ersetzt,
  Investment behält sein strategisches Gewicht (Renormierung auf 1,0).
- **ACWI-Sektorgewichte mit `asof` statt Tabelle:** Die Sektorgewichte bleiben
  ein Settings-Dict; der 120-Tage-Staleness-Check läuft über das Feld
  `risk_benchmark_sector_weights_asof`. Die Regionsgewichte liegen dagegen in
  der Tabelle `risk_benchmark_region_weights`.
