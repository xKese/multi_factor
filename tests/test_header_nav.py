"""Kopfzeilen-Navigation: Aufteilung Tab-Reihe / Utility-Menü und die
Element-IDs, auf die sich ``assets/ms-theme.js`` verlässt.

Die Überlauf-Berechnung selbst läuft im Browser und ist hier nicht testbar —
prüfbar ist aber der Vertrag zwischen Python-Markup und JavaScript: benennt
jemand eine ID um, schlägt dieser Test fehl, statt dass die Navigation im
Browser stumm ausfällt.
"""

from __future__ import annotations

import pytest

from app.ui.header import (
    MAIN_NAV_ORDER,
    UTILITY_PAGES,
    header_layout,
    split_pages,
)


ALL_PAGES = [
    {"name": name, "path": path}
    for name, path in [
        ("Dashboard", "/"),
        ("Einzelanalyse", "/einzelanalyse"),
        ("Agenten-Analyse", "/agenten-analyse"),
        ("Momentum-Monitor", "/sma"),
        ("Sektor-Momentum", "/sektor-momentum"),
        ("M&S Portfolio", "/portfolios"),
        ("Modellportfolio", "/modellportfolio"),
        ("Factor Timing", "/factor-timing"),
        ("Risiko & Benchmark", "/risiko"),
        ("Daten-Import", "/daten-import"),
        ("Einstellungen", "/einstellungen"),
        ("Perzentil-Hilfe", "/perzentil-hilfe"),
        ("Anleitung", "/anleitung"),
    ]
]


def _walk(component):
    """Alle Komponenten des Baums, Wurzel eingeschlossen."""
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "children") or hasattr(child, "_prop_names"):
            yield from _walk(child)


def _by_id(component):
    return {
        c.id: c for c in _walk(component) if getattr(c, "id", None) is not None
    }


def _classes(component, name):
    return [
        c
        for c in _walk(component)
        if name in (getattr(c, "className", None) or "").split()
    ]


def test_main_nav_has_ten_tabs():
    main, _ = split_pages(ALL_PAGES)
    assert len(main) == 10


def test_utility_pages_leave_the_tab_row():
    main, utility = split_pages(ALL_PAGES)
    assert [p["name"] for p in utility] == [
        "Einstellungen",
        "Perzentil-Hilfe",
        "Anleitung",
    ]
    assert not set(UTILITY_PAGES) & {p["name"] for p in main}


def test_every_registered_page_stays_reachable():
    """Regressionsschutz: eine neue Seite ohne NAV_ORDER-Eintrag darf nicht
    verschwinden, sondern landet über den 99-Fallback hinten in der Reihe."""
    pages = ALL_PAGES + [{"name": "Brandneu", "path": "/brandneu"}]
    main, utility = split_pages(pages)
    assert {p["name"] for p in main} | {p["name"] for p in utility} == {
        p["name"] for p in pages
    }
    assert main[-1]["name"] == "Brandneu"


def test_nav_order_matches_registered_names():
    """Tippfehler in den deutschen Seitennamen fallen sofort auf."""
    registered = {p["name"] for p in ALL_PAGES}
    assert set(MAIN_NAV_ORDER) <= registered
    assert set(UTILITY_PAGES) <= registered


def test_tab_row_follows_nav_order():
    main, _ = split_pages(ALL_PAGES)
    assert [p["name"] for p in main] == sorted(
        MAIN_NAV_ORDER, key=MAIN_NAV_ORDER.get
    )


@pytest.mark.parametrize(
    "element_id",
    [
        "ms-nav",
        "ms-nav-more",
        "ms-nav-more-btn",
        "ms-nav-more-panel",
        "ms-util",
        "ms-util-btn",
        "ms-util-panel",
        "ms-agent-status",
        "ms-data-status",
        "ms-theme-btn",
    ],
)
def test_header_provides_ids_the_javascript_needs(element_id):
    assert element_id in _by_id(header_layout(ALL_PAGES))


def test_overflow_button_is_wired_for_screenreaders():
    btn = _by_id(header_layout(ALL_PAGES))["ms-nav-more-btn"]
    assert btn.to_plotly_json()["props"]["aria-controls"] == "ms-nav-more-panel"
    assert btn.to_plotly_json()["props"]["aria-expanded"] == "false"
    assert btn.to_plotly_json()["props"]["aria-haspopup"] == "menu"


def test_overflow_panel_starts_empty():
    """Die Einträge erzeugt ms-theme.js bei jeder Neuberechnung neu."""
    panel = _by_id(header_layout(ALL_PAGES))["ms-nav-more-panel"]
    assert not getattr(panel, "children", None)


def test_utility_menu_lists_exactly_the_three_pages():
    header = header_layout(ALL_PAGES)
    items = _classes(header, "ms-util-item")
    assert [i.href for i in items] == [
        "/einstellungen",
        "/perzentil-hilfe",
        "/anleitung",
    ]
    assert all(i.to_plotly_json()["props"]["role"] == "menuitem" for i in items)


def test_tab_row_renders_ten_nav_links():
    header = header_layout(ALL_PAGES)
    links = _classes(header, "ms-nav-link")
    assert len(links) == 10
    assert links[0].href == "/"
