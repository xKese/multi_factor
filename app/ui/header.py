"""Kopfzeile: Marke, Priority+-Navigation, Statusleiste, Utility-Menü.

Die Tab-Reihe ist eine „Priority+"-Navigation: sichtbar bleibt, was in die
Breite passt; der Rest wandert in ein „Mehr ▾"-Menü am Ende der Reihe. Welche
Tabs das sind, entscheidet sich erst im Browser und hängt von Fensterbreite,
Schriftgrösse und der Breite der Statusleiste ab — die Berechnung läuft
deshalb vollständig clientseitig in ``assets/ms-theme.js``. Hier entsteht nur
das statische Markup samt leerem Dropdown-Container (gleiches Muster wie
``app/ui/command_palette.py``).
"""

from __future__ import annotations

from typing import Iterable

from dash import dcc, html


# Reihenfolge der Haupt-Tabs. Seiten ohne Eintrag landen über den
# 99-Fallback hinten in der Reihe und nehmen automatisch am Überlauf teil —
# eine neue Seite braucht hier also keinen Eintrag, um erreichbar zu sein.
MAIN_NAV_ORDER = {
    "Dashboard": 0,
    "Einzelanalyse": 1,
    "Agenten-Analyse": 2,
    "Momentum-Monitor": 3,
    "Sektor-Momentum": 4,
    "M&S Portfolio": 5,
    "Modellportfolio": 6,
    "Factor Timing": 7,
    "Risiko & Benchmark": 8,
    "Daten-Import": 9,
}

# Dauerhaft aus der Tab-Reihe ausgelagert: selten benutzte Seiten, die über
# das Zahnrad-Menü rechts neben den Status-Chips erreichbar sind.
# Wert = Reihenfolge im Menü.
UTILITY_PAGES = {
    "Einstellungen": 0,
    "Perzentil-Hilfe": 1,
    "Anleitung": 2,
}


def split_pages(pages: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Teilt die Seitenregistrierung in Tab-Reihe und Utility-Menü auf."""
    pages = list(pages)
    main = sorted(
        (p for p in pages if p["name"] not in UTILITY_PAGES),
        key=lambda p: MAIN_NAV_ORDER.get(p["name"], 99),
    )
    utility = sorted(
        (p for p in pages if p["name"] in UTILITY_PAGES),
        key=lambda p: UTILITY_PAGES[p["name"]],
    )
    return main, utility


def _nav(pages: list[dict]) -> html.Nav:
    """Tab-Reihe plus (zunächst verstecktes) Überlauf-Menü.

    Das Panel bleibt leer: ``ms-theme.js`` erzeugt seine Einträge bei jeder
    Neuberechnung aus den gerade überzähligen Tabs.
    """
    links = [
        dcc.Link(p["name"], href=p["path"], className="ms-nav-link")
        for p in pages
    ]
    more = html.Div(
        [
            html.Button(
                [
                    html.Span("Mehr", className="ms-nav-more-label"),
                    html.Span(
                        "▾",
                        className="ms-nav-more-caret",
                        **{"aria-hidden": "true"},
                    ),
                ],
                id="ms-nav-more-btn",
                className="ms-nav-more-btn",
                type="button",
                title="Weitere Seiten",
                **{
                    "aria-haspopup": "menu",
                    "aria-expanded": "false",
                    "aria-controls": "ms-nav-more-panel",
                },
            ),
            html.Div(
                id="ms-nav-more-panel",
                className="ms-nav-more-panel",
                role="menu",
                **{"aria-labelledby": "ms-nav-more-btn"},
            ),
        ],
        id="ms-nav-more",
        className="ms-nav-more is-hidden",
    )
    return html.Nav(links + [more], className="ms-nav", id="ms-nav")


def _utility_menu(pages: list[dict]) -> html.Div:
    """Zahnrad-Menü rechts: Einstellungen, Perzentil-Hilfe, Anleitung.

    Bewusst ``html.A`` statt ``dcc.Link`` — nur damit lassen sich ``role`` und
    ``tabIndex`` setzen, die das Menü-Muster verlangt. Den Seitenwechsel ohne
    Reload übernimmt ``ms-theme.js`` per ``history.pushState`` (gleicher Weg
    wie in der Command-Palette).
    """
    items = [
        html.A(
            p["name"],
            href=p["path"],
            className="ms-util-item",
            role="menuitem",
            tabIndex="-1",
        )
        for p in pages
    ]
    return html.Div(
        [
            html.Button(
                html.Span("⚙", **{"aria-hidden": "true"}),
                id="ms-util-btn",
                className="ms-util-btn",
                type="button",
                title="Einstellungen, Perzentil-Hilfe, Anleitung",
                **{
                    "aria-haspopup": "menu",
                    "aria-expanded": "false",
                    "aria-controls": "ms-util-panel",
                    "aria-label": "Einstellungen und Hilfe",
                },
            ),
            html.Div(
                items,
                id="ms-util-panel",
                className="ms-util-panel",
                role="menu",
                **{"aria-labelledby": "ms-util-btn"},
            ),
        ],
        id="ms-util",
        className="ms-util",
    )


def header_layout(pages: Iterable[dict]) -> html.Header:
    main, utility = split_pages(pages)
    return html.Header(
        [
            html.Div(
                [
                    html.Div(className="ms-brand-mark"),
                    html.Span("M&S · Multi-Faktor", className="ms-brand-text"),
                ],
                className="ms-brand",
            ),
            _nav(main),
            html.Div(
                [
                    html.Div(id="ms-agent-status"),
                    html.Div(id="ms-data-status", className="ms-data-status"),
                    _utility_menu(utility),
                    html.Button(
                        [
                            html.Span("☀", className="sun", **{"aria-hidden": "true"}),
                            html.Span("☾", className="moon", **{"aria-hidden": "true"}),
                        ],
                        id="ms-theme-btn",
                        className="ms-theme-toggle",
                        type="button",
                        title="Theme umschalten",
                        n_clicks=0,
                        **{
                            "aria-label": "Theme umschalten",
                            "aria-pressed": "false",
                        },
                    ),
                ],
                className="ms-header-tools",
            ),
        ],
        className="ms-header",
    )
