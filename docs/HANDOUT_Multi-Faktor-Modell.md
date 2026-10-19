# Handout Multi-Faktor-Modell

Interne Verwendung · Stand 06.10.2026 · Quelle: https://claude.ai/code/artifact/79d87563-0b30-42fc-96c0-626bbb565689

## 1. Zweck und Überblick

Das M&S Multi-Faktor-Modell ist unser regelbasiertes Aktien-Screening- und Scoring-System: Es bewertet rund 1.300 globale Aktien aus einem Koyfin-Export über vier Faktoren (Value, Quality, Momentum, Investment) und leitet daraus ein verbindliches Zielportfolio von 35 Titeln ab. Dieses Handout beschreibt die Systematik, die Mathematik und unseren Arbeitsablauf im Tool; Ziel ist, dass jede Kollegin und jeder Kollege den Prozess nachvollziehen und eigenständig durchführen kann.

Das Modell ist in der Python/Dash-Anwendung `multi_factor` umgesetzt und ersetzt das ursprüngliche Excel-Modell (`M&S_Multi-Faktor-Model.xlsx`) eins zu eins, erweitert um Portfoliokonstruktion, Risikomodul und Faktor-Timing. Alle Parameter sind Code-Defaults und über die Seite *Einstellungen* änderbar.

| Baustein | Was er leistet | Ergebnis |
| --- | --- | --- |
| Daten-Import | Koyfin-CSV (57 Spalten) einlesen, validieren, GICS-Industriegruppe zuordnen | bereinigtes Universum |
| Universumsfilter | Piotroski, Market Cap, Abdeckung, optional Liquidität/IPO/Verschuldung | investierbares Universum + Filtergründe |
| Scoring v2 (primär) | Z-Score-Composite aus 4 Faktoren, sequenziell neutralisiert | `composite_z`, Score 0–100, Klasse A–F, Zone |
| Scoring v1 (Vergleich) | Perzentil-Score aus 5 Faktoren innerhalb der GICS-Industrie | Gesamt-Score 0–100, Empfehlung |
| Modellportfolio | regelbasierte Konstruktion von 35 Titeln mit Bandbreiten und TE-Kontrolle | Zielportfolio, Trade-Liste |
| Risiko & Benchmark | Tracking Error ex post/ex ante, MCTE, Szenarien, Faktor-Schocks | Risikoreport |
| Faktor-Timing | taktische Tilts ±10 % um die strategischen Gewichte, seit v2 nur Monitoring | Tilt-Zerlegung je Faktor |
| Agenten-Analyse | optionale LLM-Tiefenanalyse je Titel (TradingAgents) | qualitatives Rating |

Grundprinzip: **keine stillen Fallbacks**. Jede Ausnahme im Datenfluss erzeugt eine Diagnose (Fehler, Warnung, Info), die beim Import, im Dashboard und in der Kopfzeile sichtbar ist. Das System ist Entscheidungsunterstützung, kein Auto-Trade.

## 2. Systematik des Modells

Das Modell folgt dem klassischen Faktor-Investing: Aktien mit günstiger Bewertung (Value), hoher Profitabilität und Bilanzqualität (Quality), positivem Kurs- und Gewinntrend (Momentum) und zurückhaltender Kapitalexpansion (Investment) haben historisch eine Risikoprämie erzielt. Wir messen jeden Faktor über wenige, evidenzbasierte Kennzahlen, standardisieren sie innerhalb vergleichbarer Gruppen und fassen sie zu einem Gesamtwert zusammen. Daraus entsteht ein Ranking, aus dem regelbasiert ein Portfolio gebaut wird.

Der Prozess läuft in sechs Stufen, jede Stufe ist deterministisch (gleiche Eingaben und Einstellungen ergeben identische Ergebnisse, Tie-Break über die interne Kennung `uid`):

1. **Universum laden**: Koyfin-Screener-Export mit rund 1.300 globalen Aktien und 57 Pflichtspalten, optional Zusatzspalten (EV/EBIT, Nettoverschuldung/EBITDA, FCF-Yield, Handelsumsatz, IPO-Datum). Plausibilitätsprüfung gegen Spaltenverschiebungen, Zuordnung der GICS-Industriegruppe.
2. **Kennzahlen ableiten und bereinigen**: abgeleitete Größen wie Bruttogewinn/Total Assets, Accruals, Asset Growth, Aktienemission und risikoadjustiertes 12-1-Momentum; unplausible Werte (negative Multiples, Ausreißer außerhalb definierter Bänder) werden auf NaN gesetzt, nie imputiert.
3. **Standardisieren**: je Indikator Winsorisierung und Z-Score innerhalb der GICS-Industriegruppe (Ebene 1) und anschließend innerhalb der Region (Ebene 2). So wird ein Bankentitel nur mit Banken verglichen und ein europäischer Titel nicht durch US-Bewertungsniveaus verzerrt.
4. **Aggregieren**: Faktor-Z-Score als Mittel der gültigen Indikator-Z-Scores, Composite als gewichtete Summe der vier Faktoren (Value 0,30 / Quality 0,30 / Momentum 0,25 / Investment 0,15), erneut standardisiert und in ein Perzentil 0–100 übersetzt.
5. **Filtern und zonieren**: harte Ausschlüsse (Market Cap, Piotroski, Datenabdeckung, optional Liquidität, IPO-Alter, Extremverschuldung, Overrides) und Einteilung in die Zonen KANDIDAT (Perzentil ≥ 80), HALTEN (66,7 bis 80), VERKAUFEN (< 66,7) und FILTER.
6. **Portfolio konstruieren**: Auswahl von 35 Titeln aus der KANDIDAT-Zone unter Sektor- und Regionsbandbreiten, Gewichtung nach Score und Volatilität, Ex-ante-Tracking-Error-Kontrolle, Trade-Liste mit Turnover-Budget.

*(Abbildung im Online-Dokument: Modellprozess, sechs Stufen vom Import zum Zielportfolio; die Stufen entsprechen der Liste oben.)*

Jede Stufe ist deterministisch; Ausnahmen erzeugen Diagnosen statt stiller Fallbacks, und nur die letzte Stufe erzeugt Handlungen.

Zwei Scoring-Versionen laufen parallel: **v2 (Composite, primär)** ist das beschriebene Z-Score-Verfahren mit vier Faktoren. **v1 (Vergleichsmodus)** ist das ursprüngliche Excel-Verfahren mit fünf Faktoren (Value, Quality, Growth, Momentum, Low Volatility) auf Basis von Perzentil-Rängen innerhalb der GICS-Industrie. Beide werden bei jedem Import berechnet und im Punkt-in-Zeit-Archiv gespeichert; die Einstellung `scoring_version` steuert nur die primäre Anzeige. Growth und Low Volatility sind in v2 keine Faktoren mehr: Growth entfällt ersatzlos (schwache Evidenz), Low Volatility wirkt nur noch über die Gewichtung im Portfolio, Piotroski ist ein reiner Filter. Altman Z ist weder Faktor noch Filter, sondern eine reine Informationskennzahl (für Technologie-, Software- und Dienstleistungsunternehmen nicht anwendbar).

## 3. Die vier Faktoren und ihre Indikatoren

Jeder Faktor besteht aus wenigen, gleichgewichteten Indikatoren. Die Richtung gibt an, ob ein hoher Wert gut ist (+1) oder ein niedriger (−1). Die Spalte Schema nennt die Neutralisierungsgruppe, in der der Indikator standardisiert wird (siehe Abschnitt 4).

| Faktor (Gewicht) | Indikator | Formel / Quelle | Richtung | Schema | Ökonomische Begründung |
| --- | --- | --- | --- | --- | --- |
| Value (0,30) | EV/EBITDA | Koyfin `ev_ebitda` | −1 | Industriegruppe → Region | kapitalstrukturneutrale Bewertung des operativen Ergebnisses |
| Value | FCF-Yield | `fcf_yield` (FCF/EV); Fallback je Titel `1/pfcf` (FCF/Marktkap.) | +1 | Industriegruppe → Region | Cash-basierte Bewertung, robust gegen Bilanzpolitik |
| Value | EV/EBIT (optional) | `ev_ebit`, nur wenn Spalte vorhanden | −1 | Industriegruppe → Region | Bewertung nach Abschreibungen |
| Quality (0,30) | Gross Profit / Total Assets | `(revenue − cogs) / total_assets` | +1 | Industriegruppe → Region | Novy-Marx-Profitabilität, am wenigsten manipulierbar |
| Quality | ROIC | Koyfin `roic` | +1 | Industriegruppe → Region | Kapitalrendite auf das gesamte investierte Kapital |
| Quality | Accruals | `(net_income − ocf) / total_assets` | −1 | Sektor → Region | hohe Accruals = Gewinnqualität schwach (Sloan) |
| Quality | Leverage | `net_debt_ebitda` → Fallback `total_debt / (revenue · op_margin)` → `debt_equity` | −1 | Industriegruppe → Region | Verschuldungsrisiko, Distress-Vermeidung |
| Momentum (0,25) | 12-1-Momentum, risikoadjustiert | `(ret_12m − ret_1m) / volatility_1y` | +1 | nur Region | Preistrend ohne Kurzfrist-Reversal, skaliert nach Risiko |
| Momentum | EPS-Revisionen 3M | Koyfin `eps_revisions_3m` | +1 | nur Region | Analysten-Revisionsdrift, Gewinnmomentum |
| Investment (0,15) | Asset Growth | `total_assets / total_assets_prev − 1` | −1 | Sektor → Region | aggressive Bilanzexpansion geht niedrigen Renditen voraus |
| Investment | Nettoaktienemission | `shares_out / shares_out_prev − 1` | −1 | Sektor → Region | Verwässerung signalisiert Überbewertung, Rückkäufe das Gegenteil |

**Financials** (GICS-Sektor enthält „financ“) werden auf einem eigenen, reduzierten Satz gescort, weil EV, EBITDA, Cashflow und Verschuldungskennzahlen bei Banken und Versicherern nicht sinnvoll definiert sind: Value = P/B (−1) und P/E (−1), Quality = ROE (+1, nur bei nicht-negativem Eigenkapital) und Accruals (−1), Momentum wie oben, Investment = Nettoaktienemission (−1). Die ausgeschlossenen Indikatoren fehlen auch im Abdeckungsnenner, so dass Financials keine schlechtere Datenabdeckung ausweisen. **Real Estate** wird wie Nicht-Financials behandelt, aber ohne Accruals.

**Bereinigung vor der Standardisierung** (nur auf internen Kopien, Werte werden auf NaN gesetzt und nie imputiert): negative Multiples bei P/E, P/B, P/FCF, EV/EBITDA, EV/EBIT; FCF-Yield außerhalb −0,5 bis 0,5; Nettoverschuldung/EBITDA unter −20 oder über 50; Debt/EBIT über 50; Asset Growth und Aktienemission außerhalb −0,9 bis 3,0; Accruals außerhalb −1 bis 1; Gross Profit/Assets außerhalb −1 bis 3; ROIC außerhalb −1 bis 2; EPS-Revisionen außerhalb −1 bis 1; negatives Debt/Equity. Das risikoadjustierte Momentum verlangt eine Volatilität von mindestens 0,05, sonst wird das unadjustierte 12-1-Momentum verwendet.

**Mindestabdeckung je Faktor** (sonst Faktor NaN): Nicht-Financials Value 2 Indikatoren (bei nur 2 verfügbaren: 1), Quality 2, Momentum 1, Investment 1; Financials jeweils 1. Fehlt eine optionale CSV-Spalte, gilt der dokumentierte Fallback und der Import meldet eine Info-Diagnose mit dem betroffenen Anteil.

## 4. Mathematik des Composite-Scores

Der Score eines Titels entsteht in fünf Rechenschritten: Gruppenbildung, Winsorisierung und Z-Score je Indikator, Mittelung zum Faktor, gewichtete Summe zum Composite, globale Standardisierung und Perzentil. Alle Parameter sind Einstellungen (`v2_*`), die Defaults stehen in Klammern.

**Schritt 1: Neutralisierungsgruppen.** Jeder Indikator wird nach einem Schema in bis zu zwei Ebenen standardisiert. Standard ist die sequenzielle Neutralisierung: Ebene 1 = GICS-Industriegruppe global (25 Gruppen), Ebene 2 = Region. Die Gruppe muss auf Ebene 1 mindestens 20 gültige Werte enthalten (`v2_min_group_size_l1`), sonst Fallback auf Sektor, dann global; auf Ebene 2 mindestens 30 (`v2_min_group_size_l2`), sonst entfällt die Ebene für diese Titel. Die tatsächlich verwendete Ebenenfolge wird je Titel und Indikator gespeichert (`neut_level_<indikator>`, z. B. `industry_group>region`).

**Schritt 2: Winsorisierung und Z-Score je Gruppe.** Innerhalb jeder Gruppe wird der Rohwert auf das 3-%- und 97-%-Quantil gestutzt (`v2_winsor_lower/upper`), dann standardisiert und auf ±3 begrenzt (`v2_zscore_cap`). Die Richtung d (+1 oder −1) wird nur auf Ebene 1 angewendet.

```latex
x^{w}_{i} = \min\!\big(\max(x_i,\;Q_{0{,}03}),\;Q_{0{,}97}\big)
```

```latex
z^{(1)}_{i} = d \cdot \operatorname{clip}\!\left(\frac{x^{w}_{i} - \bar{x}^{w}_{g}}{s^{w}_{g}},\,-3,\,3\right), \qquad s_g \text{ mit } ddof = 1
```

Ebene 2 wendet denselben Ablauf (ohne Richtung) auf die Z-Scores der Ebene 1 innerhalb der Region an; der Cap der letzten Ebene ist final. Hat eine Gruppe weniger als 5 gültige Werte (`v2_min_group_valid`) oder Standardabweichung 0, wird z = 0 gesetzt und eine Diagnose erzeugt. Fehlende Werte bleiben NaN; es gibt keine Median-Imputation.

**Schritt 3: Faktor-Z-Score.** Mittel der gültigen Indikator-Z-Scores des Faktors f, sofern die Mindestabdeckung aus Abschnitt 3 erfüllt ist; zusätzlich wird der Anteil gültiger Indikatoren als `cov_f` gespeichert.

```latex
z_f = \frac{1}{|I_f^{valid}|} \sum_{i \in I_f^{valid}} z_i
```

**Schritt 4: Composite.** Gewichtete Summe der vorhandenen Faktoren mit den strategischen Gewichten W (Value 0,30, Quality 0,30, Momentum 0,25, Investment 0,15; Summe muss 1,0 ± 0,001 sein). Fehlt ein Faktor, wird auf die vorhandenen Gewichte renormiert. Decken die vorhandenen Faktoren weniger als 70 % der Gewichtssumme ab (`v2_min_factor_weight`) oder fehlen Value und Quality beide, ist der Composite NaN.

```latex
\text{composite\_raw} = \frac{\sum_f z_f \, W_f \, \mathbb{1}_f}{\sum_f W_f \, \mathbb{1}_f}, \qquad \sum_f W_f \, \mathbb{1}_f \geq 0{,}70
```

**Schritt 5: Globale Standardisierung und Perzentil.** Der rohe Composite wird über das gesamte Universum ein zweites Mal winsorisiert (1 %/99 %), standardisiert und auf ±3 begrenzt. Daraus entstehen der Perzentilrang (Durchschnittsrang bei Gleichständen) und der Score 0–100.

```latex
\text{composite\_z} = \operatorname{clip}\!\left(\frac{c^{w} - \bar{c}^{w}}{s^{w}},\,-3,\,3\right), \qquad \text{composite\_score} = 100 \cdot \operatorname{rank}_{pct}(\text{composite\_z})
```

**Ergebnisgrößen je Titel**

| Größe | Bedeutung |
| --- | --- |
| `z_value`, `z_quality`, `z_momentum`, `z_investment` | Faktor-Z-Scores |
| `composite_z` | standardisierter Gesamtwert, Grundlage für Ranking und Gewichtung |
| `composite_score` | Perzentil 0–100 |
| Klasse v2 | A ≥ 90 · B+ ≥ 80 · B ≥ 66,7 · C ≥ 50 · D ≥ 33 · F (nur Anzeige) |
| `zone_v2` | KANDIDAT (Perzentil ≥ 80) · HALTEN (66,7 bis 80) · VERKAUFEN (< 66,7) · FILTER |
| `data_coverage_v2` | faktorgewichtetes Mittel der `cov_f`, fehlende Faktoren zählen 0 |
| `filter_reasons` | Liste aller verletzten Universumsfilter |

**Scoring v1 zum Vergleich.** v1 ersetzt Z-Scores durch Perzentilränge (wie Excel PERCENTRANK.INC) innerhalb der GICS-Industrie (mindestens 5 Titel, sonst Sektor, dann global) und gewichtet die Indikatoren innerhalb eines Faktors ungleich. Faktor-Score und Gesamt-Score verwenden eine dynamische Neugewichtung bei fehlenden Werten:

```latex
F_f = 100 \cdot \frac{\sum_i p_i \, w_i \, \mathbb{1}_i}{\sum_i w_i \, \mathbb{1}_i} \quad (\text{Abdeckung} \geq 50\,\%), \qquad S = \frac{\sum_f F_f \, W_f \, \mathbb{1}_f}{\sum_f W_f \, \mathbb{1}_f} \quad (\text{Abdeckung} \geq 60\,\%)
```

mit den v1-Gewichten Value 25 %, Quality 27 %, Growth 15 %, Momentum 18 %, Low Volatility 15 % und der Empfehlung STRONG BUY ≥ 80, BUY ≥ 70, HOLD ≥ 45, SELL darunter. Die Spearman-Rangkorrelation zwischen altem Neutralisierungsschema (Region × Sektor) und dem heutigen Standard lag auf dem Universum vom 10.04.2026 bei 0,914; die Umstellung wurde am 17.09.2026 vollzogen.

## 5. Datenpipeline und Tool-Architektur

Das Tool ist eine Python/Dash-Webanwendung mit einer Dash-freien Rechen-Engine in `app/core/`, zwölf Seiten in `app/pages/` und Kommandozeilen-Werkzeugen in `app/tools/` und `app/backtest/`. Persistenz läuft über SQLAlchemy (SQLite in `data/multifactor.db`, alternativ Postgres per `DATABASE_URL`). Der einzige Pflicht-Input ist der Koyfin-CSV-Export; Kursdaten für das Risikomodul kommen aus Alpha Vantage und werden nur per CLI in den Cache geladen.

| Ebene | Modul / Ort | Aufgabe |
| --- | --- | --- |
| Input | `schema.py`, `data_loader.py`, `gics.py` | 57 Pflichtspalten positional lesen, optionale Spalten per Header erkennen, Dezimalkomma parsen, Plausibilität prüfen, GICS-Industriegruppe aus `data/reference/gics_map.csv` zuordnen |
| Scoring | `scoring_v2.py`, `scoring.py`, `piotroski.py` | Composite v2, Vergleichsscore v1, Piotroski F-Score |
| Filter | `universe_filter.py`, `diagnostics.py` | harte Ausschlüsse mit `filter_reasons`, Diagnoseliste (Fehler/Warnung/Info) |
| Konstruktion | `portfolio_construction.py`, `pc_profiles.py` | Selektion, Gewichtung, TE-Kontrolle, Trade-Liste, Konstruktionsprofile |
| Risiko | `risk_metrics.py`, `risk_mcte.py`, `risk_scenarios.py`, `av_client.py`, `av_store.py` | Tracking Error ex post und ex ante, MCTE, Szenarien, Faktorschocks, Kurscache |
| Monitoring | `momentum.py`, `signal_events.py`, `sector_momentum.py`, `factor_timing.py`, `portfolio.py` | SMA-Signale, Trendphasen, Sektor-Momentum, Faktor-Timing, Handlungs-Flags |
| Reporting | `committee_report*.py`, `factsheet_pdf.py` | Investment-Komitee-Bericht (PDF und Excel), Factsheets je Aktie |
| Persistenz | `persistence.py` | Tabellen `koyfin_universe`, `koyfin_universe_history` (PIT-Archiv), `model_portfolio`, `override_register`, `app_settings`, `pc_profiles` u. a. |

**Datenfluss beim Import.** Upload auf */daten-import* → Parsen und Validieren → Industriegruppe zuordnen → v1 und v2 scoren → Filter und Zonen → Signal- und Sektorhistorie fortschreiben → Universum in `koyfin_universe` ersetzen und zusätzlich als Snapshot in `koyfin_universe_history` archivieren (Unique auf `snapshot_date` und `uid`, Re-Import ersetzt nur denselben Tag). Die Roh-CSV liegt unter `data/archive/koyfin_<datum>.csv`. Damit ist jeder Stand reproduzierbar und Backtests ab dem ersten archivierten Import möglich.

**Konfiguration.** Alle Gewichte, Schwellen und Schemata sind Felder der Dataclass `Settings` (`app/core/config.py`) und werden auf */einstellungen* editiert und in `app_settings` gespeichert. Ein SHA-256-Hash der Scoring-, Filter- und Konstruktions-Settings (`settings_hash`) sowie ein Hash des Neutralisierungsschemas (`neut_scheme_hash`) werden mit jedem Modellportfolio-Lauf und jedem Snapshot abgelegt, so dass nachvollziehbar bleibt, mit welchen Parametern ein Ergebnis entstand.

**Eindeutige Kennung.** Koyfin-Ticker tragen kein Börsensuffix, verschiedene Aktien können dasselbe Symbol haben (z. B. SAN für Sanofi und Banco Santander). Intern führt das Tool jede Zeile unter einer `uid`: bei eindeutigem Ticker gleich dem Ticker, bei Kollision `TICKER~namens-slug`. Watchlists mit kollidierenden Tickern brauchen eine Namensspalte, sonst wird die Position als mehrdeutig markiert und nicht gematcht.

**Externe Dienste.** Alpha Vantage (Premium-Key in `ALPHAVANTAGE_API_KEY`) liefert Tageskurse, FX, Treasury-Renditen und WTI für Risikomodul und Makrodaten; alle Reihen werden nach EUR umgerechnet. Der optionale TradingAgents-Service (`TRADINGAGENTS_URL`) führt die LLM-Tiefenanalyse aus und erhält unseren Quant-Score als Prior. Ohne diese Dienste bleibt die App voll funktionsfähig, nur die jeweiligen Seiten sind deaktiviert.

## 6. So nutzen wir das Tool

Der Regelprozess besteht aus Import, Prüfung der Diagnosen, Analyse, Modellportfolio-Lauf und Komitee-Bericht. Die App startet lokal mit `python -m app.main` (Port 8050) oder per `docker compose up --build` (Port 5000).

1. **Koyfin-Export erstellen.** Koyfin → Screener → Universum wählen → die 57 Spalten in der festgelegten Reihenfolge exportieren (Liste auf der Seite *Anleitung* und in `app/core/schema.py`). Optional an beliebiger Position ergänzen: `EV/EBIT`, `Net Debt/EBITDA`, `FCF Yield (EV)`, `ADV 3M` oder `Avg Volume`, `IPO Date`, `SMA (20D)`. Prozentwerte als Dezimalanteil, Dezimaltrennzeichen Komma.
2. **Import auf */daten-import*.** CSV per Drag & Drop hochladen. Der Import lehnt Dateien mit erkennbaren Spaltenverschiebungen ab (Median |Return 12M| > 5, Kurs/SMA-200 außerhalb 0,2 bis 5, Median |Beta| > 5). Danach **Importbericht und Diagnoseliste lesen**: Fehler müssen vor der weiteren Nutzung geklärt werden, Warnungen (z. B. Industrie nicht im GICS-Mapping, Ticker-Kollision, Fallback-Anteil über 25 %) werden notiert, Infos dokumentieren Fallbacks.
3. **Dashboard (*/*).** Universums-KPIs, Ranking nach `composite_score`, Filter nach Zone, Klasse, Sektor, Region und Industriegruppe, aufklappbarer Vergleich zu Scoring v1. Kandidatenliste = Zone KANDIDAT.
4. **Einzelanalyse (*/einzelanalyse*).** Je Titel alle Indikatoren mit Z-Score, verwendeter Neutralisierungsebene und Gruppengröße, Faktorprofil, Filtergründe, Peers (ähnlichste Titel im 4-dimensionalen Z-Score-Raum), SMA-Signal und Trendphase, optional Factsheet-PDF und Agenten-Tiefenanalyse. Hier prüfen wir Kandidaten vor einer Aufnahme qualitativ.
5. **Momentum-Monitor (*/sma*) und Sektor-Momentum (*/sektor-momentum*).** Golden/Death Cross, Trendphasen (frisch, etabliert, ermüdet), 12-1-Ranking, Signalwechsel seit letztem Import; Sektoraggregate mit Breadth und Rotations-Quadrant. Beides ist Information, kein Verkaufsauslöser in v2.
6. **Portfolios (*/portfolios*).** Bestand als Koyfin-Watchlist-CSV hochladen (Spalte `Ticker`, optional `Name` und `Gewicht`). Mehrere benannte Portfolios möglich; das aktive Portfolio gilt für Portfolio-Monitor und Risiko-Seite. Die Seite zeigt Handlungs-Flags je Position in Dringlichkeitsreihenfolge: SELL, Filter nicht bestanden, Death Cross, unter SMA-200, ermüdet, Signal neu, ergänzt um die v2-Zonen-Flags.
7. **Modellportfolio (*/modellportfolio*).** Bestandsportfolio im Dropdown wählen, Rebalance-Modus prüfen (automatisch erkannt, manuell überschreibbar), Lauf starten. Ergebnis: Zielportfolio, Trade-Liste, Exposures gegen Benchmark, Ex-ante-TE, Diagnosen, Override-Register, Historie und Track Record des Paper-Portfolios. Details in Abschnitt 7 und 8.
8. **Risiko & Benchmark (*/risiko*).** Zeigt ausschließlich gecachte Daten. Vorher per CLI aktualisieren:

```bash
export ALPHAVANTAGE_API_KEY=...
python -m app.tools.risk_report update
python -m app.tools.risk_report report --asof 2026-10-06
```

9. **Bericht (*/bericht*).** Investment-Komitee-Bericht mit Key Findings, Universum, Veränderungen gegen Vorimport und gegen den Stand vor etwa einem Monat (Mover, Zonen-Matrix, neue und ausgeschiedene Kandidaten, Ranking-Stabilität, Watchlist nahe Ein- und Ausstieg), Sektoren, Portfolio, Modellportfolio mit Trade-Liste und Overrides, Datenqualität und Parameter-Anhang. Export als PDF und Excel-Anhang.
10. **Einstellungen (*/einstellungen*).** Alle Gewichte, Schwellen, Neutralisierungsschemata, Filter und Konstruktionsparameter. Änderungen an Scoring- oder Filterparametern gelten für das gesamte Universum und sind zu dokumentieren (Änderungsprotokoll in `MODEL_DESCRIPTION.md`, Abschnitt 15).

**CLI-Werkzeuge für wiederkehrende Aufgaben**

| Befehl | Zweck |
| --- | --- |
| `python -m app.tools.model_portfolio build [--mode full\|interim\|monitor] [--portfolio ID\|NAME] [--dry-run]` | Zielportfolio ohne UI; Report `reports/modellportfolio_<datum>.md`; Exit 0/1/2 bei keine/Warnung/Fehler |
| `python -m app.tools.model_portfolio compare --v1 --v2` | Rangvergleich v1 gegen v2 |
| `python -m app.tools.model_portfolio compare --scheme-a … --scheme-b …` | Vergleich zweier Neutralisierungsschemata (Pflicht vor einer Umstellung) |
| `python -m app.tools.indicator_correlation` | Spearman-Matrix und Clustering aller Indikatoren |
| `python -m app.tools.risk_report update` / `report` | Kurscache füllen, Risikoreport schreiben |
| `python -m app.backtest fetch \| run \| report` | Backtest auf US-Teiluniversum |
| `python -m app.backtest paper update` / `paper report` | Paper-Portfolio täglich fortschreiben (z. B. per Cron werktags 22:15) |
| `python -m pytest tests -q` | Testsuite vor jeder Parameter- oder Codeänderung |

**Interpretation der Ausgaben.** Ein hoher `composite_score` bedeutet relative Attraktivität im aktuellen Universum, nicht absolute Unterbewertung: Ein anderer Export ergibt andere Perzentile. Die Zone ist die Handlungsgröße, nicht die Klasse. Ein Titel in HALTEN wird nicht gekauft, aber auch nicht verkauft (Pufferzone). `trend_warning` (aktives Death Cross) ist ein Hinweis zur manuellen Prüfung, kein Signal.

## 7. Portfoliokonstruktion

Das Modellportfolio ist ein regelbasiertes Zielportfolio von 35 Titeln (Band 25 bis 40), das aus der KANDIDAT-Zone gebaut, nach Score und Volatilität gewichtet und auf einen Ex-ante-Tracking-Error von 4,5 bis 5,5 % gegen den MSCI ACWI gesteuert wird. Alle Parameter (`pc_*`) sind in Konstruktionsprofilen versionierbar und je Bestandsportfolio zuweisbar.

**Schritt 1: Universumsfilter** (harte Ausschlüsse, alle Verstöße werden in `filter_reasons` protokolliert):

| # | Filter | Regel | Fehlende Daten |
| --- | --- | --- | --- |
| 1 | Market Cap | ≥ 1.000 Mio EUR | nicht eligible |
| 2 | Piotroski | ≥ 5 von 9, Financials ≥ 3,33 von 6 | nicht eligible |
| 3 | Liquidität | Tagesumsatz 3M ≥ 2,0 Mio EUR, nur wenn Spalte vorhanden | Filter übersprungen |
| 4 | Abdeckung | `data_coverage_v2` ≥ 0,6 und Composite nicht NaN | nicht eligible |
| 5 | IPO-Alter | Erstnotiz ≥ 365 Tage vor Snapshot, nur wenn Spalte vorhanden | Filter übersprungen |
| 6 | Extremverschuldung | Nicht-Fin: Debt/Equity > 3,0 und Zinsdeckung < 2,0 | greift nicht |
| 7 | Override | aktiver Override mit Richtung `exclude` | — |

Der Altman Z-Score ist bewusst kein Filterkriterium: Seine Gewichte sind auf klassische Industriebilanzen kalibriert und liefern für Technologie-, Software- und Dienstleistungsunternehmen (ebenso Financials und Real Estate) keine belastbare Aussage. Er wird nur informativ ausgewiesen.

**Schritt 2: Selektion** (`select_portfolio`). Gehaltene Titel in KANDIDAT oder HALTEN sowie Include-Overrides bleiben im Portfolio, alle anderen gehaltenen Titel werden verkauft. Dann werden Kandidaten aus der KANDIDAT-Zone in absteigender Reihenfolge von `composite_z` aufgenommen, bis die Zielanzahl 35 erreicht ist. Ein Kandidat wird nur aufgenommen, wenn mit den vorläufigen Gewichten das Sektor- und das Regionsband (±10 Prozentpunkte gegen die Benchmark) und das Maximum von 8 Titeln je Sektor eingehalten bleiben. Geprüft wird nur der Sektor und die Region des Kandidaten; andere Gruppen können durch eine Aufnahme nur verwässert werden. Das Modell **verkauft nie wegen einer Bandbreite**; eine Verletzung aus der Pufferzone wird nur als Warnung ausgewiesen. Unter 25 Titeln greift die Notfüllzone (Perzentil 70 bis 80) mit Warnung; bleibt das Portfolio trotzdem unter 25, erzeugt der Lauf eine Fehler-Diagnose, gibt das Zielportfolio aber aus.

**Benchmark für die Bänder** (`pc_benchmark_source`): Standard `universe` = marktkapitalisierungsgewichtete Sektor- und Regionsanteile des gesamten Imports, bei jedem Import neu berechnet. Alternative `static` = gepflegte ACWI-Sektorgewichte (Settings) und Regionsgewichte (Tabelle `risk_benchmark_region_weights`); sind sie älter als 120 Tage, wird das Band ausgesetzt und gewarnt. Die TE-Kontrolle rechnet in beiden Fällen gegen die ACWI-Kurszeitreihe.

**Schritt 3: Gewichtung** (`compute_weights`). Gewichte steigen mit dem Score und sinken mit der Volatilität:

```latex
w^{raw}_i = \frac{1 + \operatorname{clip}(z_i,\,0,\,3)}{\operatorname{clip}(\sigma_i,\,0{,}10,\,0{,}60)}, \qquad w_i = \frac{w^{raw}_i}{\sum_j w^{raw}_j}
```

Danach iterativ (bis 50 Durchläufe) auf den Korridor 2 % bis 5 % je Titel geclippt, Überschuss und Defizit proportional auf nicht gebundene Titel verteilt, bis die Summe 1 ist. Fehlende Volatilität wird durch den Portfolio-Median ersetzt (Info). Ist Floor × N > 1 oder Cap × N < 1, ist der Korridor unerfüllbar und es gibt eine Fehler-Diagnose.

**Schritt 4: Ex-ante-TE-Kontrolle** (`apply_te_constraint`). Kovarianzmatrix der aktiven Tagesrenditen (Titel minus ACWI, in EUR) über 504 Handelstage mit Ledoit-Wolf-Shrinkage. Tracking Error und Risikobeiträge je Titel:

```latex
TE = \sqrt{252\, w^{\top} \Sigma w}, \qquad MCTE_i = \frac{252\,(\Sigma w)_i}{TE}, \qquad CTE_i = w_i \cdot MCTE_i, \qquad \sum_i CTE_i = TE
```

Solange TE > 6,0 % oder der größte Einzelbeitrag CTE\_i/TE > 15 % ist, wird der Titel mit dem höchsten CTE um 0,5 Prozentpunkte reduziert (nicht unter den Floor) und das Gewicht gleichmäßig auf die drei Titel mit dem niedrigsten CTE verteilt (nicht über den Cap), maximal 40 Iterationen. Bleibt die Restriktion danach verletzt, wird der letzte Stand mit der Fehler-Diagnose „TE-Restriktion nicht erfüllbar“ ausgegeben, ein Pflichtpunkt für das Investmentkomitee. Liegt der TE unter 4,5 %, gibt es eine Info. Deckt der Kurscache weniger als 60 % des Portfoliogewichts ab, wird der Schritt übersprungen und als „TE nicht prüfbar“ gewarnt.

**Overrides** (Tabelle `override_register`). Drei Richtungen: `exclude` (Titel wird gefiltert), `include` (Titel bleibt bzw. kommt ins Portfolio), `weight` (Gewicht fixiert, nimmt nicht an Korridor und TE-Kontrolle teil; Rest wird auf 1 minus Override-Gewichte renormiert). Pflichtfelder: Begründung mit mindestens 20 Zeichen, Verantwortliche Person, Ablaufdatum höchstens 180 Tage nach Anlage. Abgelaufene Overrides werden beim nächsten Import deaktiviert und als Diagnose gemeldet. Historisiert werden immer beide Gewichte: `weight_model` ohne und `weight_effective` mit Overrides.

## 8. Portfoliomanagement und Rebalancing

Das Portfolio wird zweimal im Jahr vollständig rebalanciert (März und September), zweimal im Jahr nur bereinigt (Juni und Dezember) und dazwischen überwacht. Jeder Lauf erzeugt eine Trade-Liste, die ein einseitiges Turnover-Budget einhält; was nicht ins Budget passt, wird verschoben.

**Rebalance-Modi** (`detect_rebalance_mode`, automatisch aus `snapshot_date` und letztem gespeicherten Lauf abgeleitet, auf der Seite manuell überschreibbar und protokolliert):

| Modus | Auslöser | Was passiert | Turnover-Budget (einseitig) |
| --- | --- | --- | --- |
| `full` | erster Import nach dem letzten Werktag im März oder September; ohne Vorversion immer | komplette Neukonstruktion: Selektion, Gewichtung, TE-Kontrolle | 20 % |
| `interim` | erster Import nach dem letzten Werktag im Juni oder Dezember | nur Verkäufe (VERKAUFEN, FILTER, Exclude) und deren Ersatz aus KANDIDAT; bestehende Gewichte bleiben, freiwerdendes Gewicht geht gleichmäßig an die Ersatztitel | 10 % |
| `monitor` | jeder andere Import | kein Zielportfolio-Update; Filter-Verstöße gehaltener Titel als „Sofortmaßnahme-Vorschlag“ | — |

Sind Full- und Interim-Trigger gleichzeitig überfällig, hat `full` Vorrang. Der „letzte Handelstag“ wird als letzter Werktag Montag bis Freitag genähert, ohne Feiertagskalender.

**Trade-Liste und Turnover-Budget** (`build_trade_list`). Einseitiger Turnover = 0,5 · Σ|Δw|. Überschreitet die Liste das Budget, werden Trades in dieser Priorität behalten und alle weiteren als VERSCHOBEN gestrichen:

1. Verkäufe wegen FILTER (Pflicht, immer ausgeführt)
2. Verkäufe wegen Override `exclude` (Pflicht)
3. Verkäufe der Zone VERKAUFEN, aufsteigend nach `composite_z` (schwächster zuerst)
4. Käufe aus KANDIDAT, absteigend nach `composite_z`
5. Gewichtsanpassungen, absteigend nach |Δw|

Aktionen: KAUF, VERKAUF, AUFSTOCKEN, REDUZIEREN, HALTEN, VERSCHOBEN, jeweils mit Begründung. Eine Gewichtsänderung unter 0,5 Prozentpunkten zählt nicht als Trade. Nach dem Streichen werden die umgesetzten Positionen renormiert. Verschobene Trades werden beim nächsten Lauf erneut geprüft.

**Pufferzone als Kern der Turnover-Kontrolle.** Einstieg erst ab Perzentil 80, Ausstieg erst unter 66,7. Ein gehaltener Titel, der von 85 auf 72 fällt, bleibt im Portfolio; er wird erst verkauft, wenn er unter 66,7 rutscht oder einen Filter verletzt. Das verhindert Flackern an einer einzelnen Schwelle. Die Zone HALTEN gilt nur für gehaltene Titel, neue Käufe kommen ausschließlich aus KANDIDAT.

**Laufendes Monitoring zwischen den Terminen**

- Jeder Import im Modus `monitor` prüft gehaltene Titel gegen die Filter; ein Verstoß erscheint als Sofortmaßnahme-Vorschlag in Diagnose und Komitee-Bericht und wird manuell entschieden.
- Der Portfolio-Monitor zeigt Handlungs-Flags in Dringlichkeitsreihenfolge; Death Cross und SMA-200-Unterschreitung sind Hinweise zur Prüfung, kein Verkaufsauslöser.
- Das Risikomodul liefert Tracking Error ex post (rollierend 1 und 3 Jahre), Information Ratio, aktives Beta, Up-/Downside-Capture, MCTE je Titel, aktive Sektorallokation sowie historische Szenario-Replays (GFC, Eurokrise, COVID, Zinsjahr 2022, Vol-Schock 2018) und Faktorschocks (Zinsen +100 bp, Öl +20 %, USD −10 %, Markt −15 %, Stagflation). Szenarien mit weniger als 60 % Kursabdeckung gelten als nicht belastbar.
- Das Paper-Portfolio schreibt ab jedem gespeicherten Full- oder Interim-Lauf zwei Varianten täglich fort (`model` ohne Overrides, `effective` mit Overrides); die Differenz ist der Override-Beitrag und wird im Track Record auf */modellportfolio* ausgewiesen.
- Faktor-Timing läuft im Modus `monitor`: taktische Tilts (±10 % um die strategischen Gewichte aus Makro-Regime, Faktor-Momentum und Sentiment) werden berechnet und angezeigt, fließen aber nicht ins Composite ein.

**Umgang mit Datenfehlern und Ausreißern.** Unplausible Werte werden auf NaN gesetzt, nie imputiert, und senken die Datenabdeckung; unter 60 % Abdeckung ist ein Titel nicht eligible. Fehler-Diagnosen (unerfüllbare TE-Restriktion, Portfolio unter 25 Titeln, unerfüllbarer Gewichtskorridor, ungültige Gewichtssumme) sind Pflichtpunkte für das Investmentkomitee und werden mit dem Lauf gespeichert (`model_portfolio_meta.diagnostics`). Jeder Lauf trägt `settings_hash`, `neut_scheme_hash`, Profil und Bestandsportfolio, so dass ein Ergebnis später exakt reproduziert werden kann.

## 9. Grenzen, Annahmen und offene Punkte

Das Modell ist eine Entscheidungsunterstützung mit bewusst engen Spielräumen; seine Aussagekraft hängt an konsistenten Koyfin-Exporten und an der Disziplin, Parameter nicht an Ergebnisse anzupassen.

- **Relative Scores.** Perzentile und Z-Scores beziehen sich auf das jeweils geladene Universum. Ein anderer Screener-Umfang verschiebt alle Werte; Vergleiche über die Zeit setzen gleiche Exportdefinitionen voraus.
- **Ein Stichtag je Import.** Es gibt keine Kurs- oder Fundamentalhistorie im Universum selbst, nur das Punkt-in-Zeit-Archiv ab dem ersten Import. Backtests vor diesem Datum laufen über die separate Backtest-Engine auf US-Daten mit nachträglich korrigierten Fundamentals (Restatement-Bias zugunsten von Value, Quality und Investment).
- **Erwartete Live-Prämien liegen deutlich unter Backtest-Werten.** Der Vorbehaltsblock jedes Backtest-Reports nennt McLean/Pontiff 2016: im Mittel 26 % Abschlag out-of-sample, 58 % nach Publikation.
- **FCF-Yield-Fallback.** Fehlt die Spalte FCF/EV, wird je Titel 1/P-FCF (FCF/Marktkapitalisierung) verwendet, konzeptionell abweichend; der Anteil wird in der Diagnose ausgewiesen.
- **Financials und REITs.** Bankspezifische Kennzahlen (CET1, Cost-Income-Ratio, Zinsmarge, NPL-Quote, P/TBV) und REIT-Kennzahlen (P/FFO, NAV-Abschlag) sind im Export nicht enthalten. Banken werden fair gegen Banken gerankt, aber nicht auf regulatorische Kapitalstärke geprüft; dafür sind Agenten-Analyse oder manuelle Prüfung gedacht.
- **Benchmark-Quelle `universe`.** Sektor- und Regionsbänder vergleichen gegen das eigene marktkapitalisierungsgewichtete Universum, das sich mit jedem Import ändert. Die TE-Kontrolle rechnet dagegen immer gegen die ACWI-Kurszeitreihe in EUR ohne Währungssicherung.
- **Rebalance-Kalender.** Letzter Werktag statt letzter Handelstag, kein Feiertagskalender.
- **Kleine Regionen.** Africa / Middle East und Latin America unterschreiten die Mindestgröße für Ebene 2; dort entfällt die Regionsneutralisierung und die Rangkorrelation zum Altschema ist niedriger (0,75 bis 0,76).
- **Faktor-Timing.** Schwache Out-of-Sample-Evidenz, deshalb nur Monitoring; Faktor-Momentum ist ein Querschnitts-Proxy (Quintilsspread der 6M-Returns), Value-Spread liefert mangels Historie keinen Tilt.
- **Steuern und Kosten.** Die Trade-Liste berücksichtigt keine Steuerlots (Schnittstelle `tax_lots` ist vorbereitet, aber inaktiv); Backtest-Kosten sind pauschal 10 bp Kommission plus 5 bp Slippage je Seite, ohne Marktimpact.
- **Parameterdisziplin.** Faktorgewichte, Schwellen und Bandbreiten wurden vor dem Backtest festgelegt. Jede spätere Anpassung ist im Änderungsprotokoll zu dokumentieren und per Vergleichs-CLI zu belegen.

Offene Punkte, die im Komitee regelmäßig zu prüfen sind: Fallback-Anteile je Indikator (Warnung ab 25 %), Alter der statischen Benchmark-Gewichte (120 Tage), abgelaufene Overrides und wiederholt verschobene Trades.

## 10. Glossar und Kurzreferenz

| Begriff | Bedeutung im Modell |
| --- | --- |
| Composite-Z / Composite-Score | gewichteter, global standardisierter Gesamtwert der vier Faktoren bzw. sein Perzentil 0–100 |
| Zone | Handlungsgröße je Titel: KANDIDAT (≥ 80), HALTEN (66,7 bis 80), VERKAUFEN (< 66,7), FILTER |
| Klasse | Anzeigegröße A, B+, B, C, D, F nach Perzentil; keine Handlungsfolge |
| Neutralisierung | Standardisierung eines Indikators innerhalb einer Vergleichsgruppe (Industriegruppe, Sektor, Region, global) |
| Sequenzielle Neutralisierung | Ebene 1 Industriegruppe global, Ebene 2 Region; Schema je Indikator wählbar |
| Winsorisierung | Stutzen der Rohwerte auf das 3-%- und 97-%-Quantil vor dem Z-Score (Composite: 1 %/99 %) |
| Z-Score | (Wert minus Gruppenmittel) / Gruppenstandardabweichung, begrenzt auf ±3 |
| Richtung | +1 hoher Wert gut, −1 niedriger Wert gut; wirkt nur auf Ebene 1 |
| Datenabdeckung | Anteil gültiger Indikatoren je Faktor (`cov_f`) bzw. faktorgewichtet je Titel (`data_coverage_v2`); Mindestwert 0,6 für Eligibilität |
| Piotroski F-Score | 9 Bilanzkriterien (Financials 6), Filter ab 5 bzw. 3,33 Punkten |
| Altman Z | Insolvenzindikator, nur informativ (kein Filter, da u. a. für Technologieunternehmen nicht anwendbar) |
| Pufferzone | Band 66,7 bis 80, in dem gehaltene Titel bleiben, aber nicht gekauft werden |
| Notfüllzone | Band 70 bis 80, aus dem nur bei weniger als 25 Titeln nachgefüllt wird |
| Sektor-/Regionsband | ±10 Prozentpunkte um das Benchmark-Gewicht, max. 8 Titel je Sektor |
| Tracking Error (TE) | annualisierte Standardabweichung der aktiven Rendite gegen ACWI; ex post aus Historie, ex ante aus Kovarianz |
| MCTE / CTE | marginaler bzw. absoluter Risikobeitrag eines Titels zum TE; Summe der CTE ist der TE |
| Ledoit-Wolf | Shrinkage-Schätzer der Kovarianzmatrix, 504 Handelstage |
| Turnover (einseitig) | 0,5 · Σ |
| Override | dokumentierte manuelle Abweichung: exclude, include oder weight; befristet auf 180 Tage |
| Konstruktionsprofil | benannte Version der `pc_*`-Parameter, je Bestandsportfolio zuweisbar |
| PIT-Archiv | Punkt-in-Zeit-Snapshot jedes Imports in `koyfin_universe_history` plus Roh-CSV |
| uid | interne eindeutige Kennung; Ticker oder `TICKER~namens-slug` bei Kollision |
| Diagnose | protokollierte Ausnahme mit Schweregrad Fehler, Warnung oder Info; es gibt keine stillen Fallbacks |
| 12-1-Momentum | Return 12 Monate minus Return 1 Monat, im Composite durch die Volatilität geteilt |
| Golden / Death Cross | Kurs und SMA-50 beide über bzw. unter dem SMA-200; zustandsbasiert, nur Information |

**Kennzahlen auf einen Blick**

| Parameter | Default |
| --- | --- |
| Faktorgewichte V / Q / M / I | 0,30 / 0,30 / 0,25 / 0,15 |
| Winsor je Indikator / Composite | 3 % und 97 % / 1 % und 99 % |
| Z-Score-Cap | ±3 |
| Mindestgruppengröße Ebene 1 / Ebene 2 | 20 / 30 gültige Werte |
| Mindestgewicht vorhandener Faktoren | 0,70 |
| Einstieg / Ausstieg / Notfüllung | Perzentil 80 / 66,7 / 70 |
| Zielanzahl / Minimum / Maximum | 35 / 25 / 40 Titel |
| Gewichtskorridor je Titel | 2 % bis 5 % |
| Volatilitätskorridor für Gewichtung | 10 % bis 60 % |
| TE-Zielband / Obergrenze / max. CTE-Anteil | 4,5 bis 5,5 % / 6,0 % / 15 % |
| Rebalancing full / interim | März, September / Juni, Dezember |
| Mindest-Trade | 0,5 Prozentpunkte |

Quellen: `MODEL_DESCRIPTION.md`, `README.md` und die Module unter `app/core/` im Repository `xKese/multi_factor`, Stand 06.10.2026.
