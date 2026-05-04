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
  Drilldown (klickbare Balken + Filter-Chip) (siehe Git-Historie).

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

## Allgemeine offene Punkte

- **Responsives Verhalten < 1024 px** ist nicht getestet. KPI-Band hat
  einen Breakpoint bei 1100 px, Header nicht. Mobile-Fähigkeit
  explizit definieren oder als "Desktop-Only" dokumentieren.
- **Dash-Table-Filter-Syntax** ist für Endnutzer kryptisch
  (`>=3`, `contains "Tech"`). Eventuell Tooltip mit Beispielen einbauen.
- **Einstellungen-Save-Bestätigung** verschwindet nicht — wird nach
  zweitem Save überschrieben, wirkt aber „stale". Timeout einbauen.
