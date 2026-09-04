# UI/UX-Backlog — M&S Multi-Faktor-Modell

Offene Verbesserungen, die in späteren Sessions umgesetzt werden sollen.
Jeder Eintrag enthält genug Kontext, um ohne Rückfragen direkt starten zu
können: **Was fehlt**, **Warum**, **Aktueller Code-Stand**, **Ansatz**,
**Akzeptanzkriterien**, **Aufwand**.

Abgeschlossen (nicht mehr hier):
- Ticker-Links in Tabellen · Datenstatus in Kopfzeile · Leer-Zustand
  Dashboard · Einstellungen-Summenvalidierung · Globale Ticker-Suche
  (Cmd+K / `/`) · Tabellen-Export (xlsx) · Einzelanalyse-Rückblicks-
  fenster (Übergangs-Variante — siehe unten) · Theme-Toggle Sun/Moon +
  A11y · Portfolio-Input-Validierung + Feedback · Dashboard-Sektor-
  Drilldown (klickbare Balken + Filter-Chip) · Einstellungen-Save-
  Bestätigung blendet nach 4 s automatisch aus (siehe Git-Historie).

### Offene Datenfrage (nicht blockierend)
- **Echte Sparklines** für die Einzelanalyse benötigen Monats-
  Schlusskurse, die der Koyfin-Export aktuell nicht liefert. Aktuell
  wird die Übergangs-Variante gerendert: 4 disjunkte Return-Fenster
  (1M/3M/6M/12M) als „Rückblicksfenster"-Mikrografik in
  `_rueckblick()` (`app/pages/einzelanalyse.py`). Sobald der CSV-Export
  um Preis-Zeitreihen erweitert werden kann, sollte diese Funktion
  durch eine echte Sparkline ersetzt werden — Schema-Anpassung in
  `app/core/schema.py::KOYFIN_COLUMNS` + Loader-Erweiterung.

---

## Scoring v2 — Design-Integration (erledigt, Rest offen)

Erledigt (siehe Git-Historie): v2-Labels/Formatierung im Design-System
(`labels.py`, `formatters.py`, `common.py`), Zonen-/Klassen-/Action-Farben
(CSS `ms-zone-*`/`ms-diag-*` + `style_data_conditional`), Diagnose-Panel
(`diagnostics_panel`) auf Daten-Import/Dashboard/Modellportfolio +
Kopfzeilen-Chip mit Scoring-Version, v2-Primäranzeige auf Dashboard-Hero,
Einzelanalyse-Hero, SMA-Signale, Portfolios, Risiko, Sektor-Momentum
(getrennte v2-History-Levels), Agenten-Analyse (Live-Quant-Badge),
Perzentil-Hilfe/Anleitung, Factsheet-PDF-v2-Sektion, README.

Offen:
- **Zonen-Übergangs-Historie**: kleine Zeitreihe „Zone je Titel über
  Snapshots" (PIT-Archiv vorhanden, `koyfin_universe_history`) — z. B.
  Zonen-Wechsel-Badge in der Einzelanalyse.
- **Sektor-Momentum v2-Sparklines**: Die v2-Delta-Reihe (`sector_v2`-Level
  in `sector_score_history`) baut sich erst ab dem nächsten Import auf;
  bis dahin zeigen Delta/Spark im v2-Modus „–".
- **Agenten-PDF**: v2-Kontext erscheint als Zusatzzeile; eine eigene
  v2-Kennzahlen-Sektion (Faktor-Z je Agent-Report) wäre denkbar.

## Allgemeine offene Punkte

- **Responsives Verhalten < 1024 px** ist nicht getestet. KPI-Band hat
  einen Breakpoint bei 1100 px, Header nicht. Mobile-Fähigkeit
  explizit definieren oder als "Desktop-Only" dokumentieren.
- **Dash-Table-Filter-Syntax** ist für Endnutzer kryptisch
  (`>=3`, `contains "Tech"`). Eventuell Tooltip mit Beispielen einbauen.
