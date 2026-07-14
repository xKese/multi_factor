# Factsheet-Schriftarten (optional)

Der PDF-Export der Einzelanalyse rendert mit folgenden Schriftarten:

- **Inter** (sans-serif body)
- **Source Serif 4** (serif display + italic these)

Beide sind nicht im Repo gebündelt (Lizenz/Größe). Solange sie fehlen, fällt
WeasyPrint auf System-Fonts zurück (`DejaVu Sans` / `DejaVu Serif`). Das
Layout bleibt identisch, die Typografie weicht jedoch leicht ab.

## Für pixel-genaues Rendering

Lade die OFL-lizenzierten TTFs herunter und lege sie in dieses Verzeichnis
mit folgenden exakten Dateinamen ab:

```
Inter-Regular.ttf
Inter-Medium.ttf
Inter-SemiBold.ttf
Inter-Bold.ttf
SourceSerif4-Regular.ttf
SourceSerif4-Italic.ttf
SourceSerif4-SemiBold.ttf
```

Quellen:

- Inter: <https://github.com/rsms/inter/releases>
- Source Serif 4: <https://github.com/adobe-fonts/source-serif/releases>

Beide unter SIL Open Font License (OFL) — die Lizenztexte sollten ebenfalls
in diesem Verzeichnis abgelegt werden (`OFL-Inter.txt`, `OFL-SourceSerif4.txt`).

Die `@font-face`-Regeln in `app/factsheet_template/editorial.css` greifen
diese Dateien automatisch auf, sobald sie vorhanden sind — kein Code-Change
nötig.

Das Verzeichnis liegt bewusst **ausserhalb** von `app/assets/`, weil Dash
jede CSS/JS-Datei aus seinem assets-Ordner automatisch in alle Seiten der
App lädt — das Factsheet-CSS (mit `@page` und `body { width: 794px }`)
würde dann die ganze Anwendung auf A4-Format zwingen.
