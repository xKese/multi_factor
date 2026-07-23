"""Tests für die Autocomplete-Optionen der Ad-hoc-Agenten-Analyse."""

from __future__ import annotations

# register_page() im Seitenmodul verlangt eine instanziierte Dash-App.
from app.main import create_app

create_app()

from app.pages import agenten_analyse as aa  # noqa: E402


_RESULTS = [
    {"symbol": "MBG.F", "name": "Mercedes-Benz Group AG", "region": "Frankfurt"},
    {"symbol": "MBG.MX", "name": "Mercedes-Benz Group AG", "region": "Mexico"},
]


def test_symbol_options_maps_results():
    options = aa._symbol_options(_RESULTS, "mercedes")
    values = [o["value"] for o in options]
    assert values == ["MBG.F", "MBG.MX"]
    assert "Mercedes-Benz Group AG (Frankfurt)" in options[0]["label"]


def test_symbol_options_tickerlike_term_prepended():
    # Tickerartige Eingaben bleiben direkt wählbar (Freitext-Fallback,
    # z. B. ohne Alpha-Vantage-Key oder ohne Treffer).
    options = aa._symbol_options([], "googl")
    assert options[0]["value"] == "GOOGL"
    assert "direkt übernehmen" in options[0]["label"]

    # Kein Duplikat, wenn der Begriff bereits in den Treffern steckt.
    options2 = aa._symbol_options(_RESULTS, "MBG.F")
    assert [o["value"] for o in options2].count("MBG.F") == 1


def test_symbol_options_non_tickerlike_term_not_prepended():
    # Namensartige Begriffe (lang, Sonderzeichen) erzeugen keine Pseudo-Option.
    for term in ("mercedes", "mercedes benz!"):
        options = aa._symbol_options(_RESULTS, term)
        assert all("direkt übernehmen" not in o["label"] for o in options)

    # Suffigierte Ticker über 6 Zeichen bleiben direkt wählbar.
    options = aa._symbol_options([], "MBG.DE")
    assert options[0]["value"] == "MBG.DE"


def test_symbol_options_keeps_current_selection():
    options = aa._symbol_options(_RESULTS, "merc", current="AAPL")
    assert options[-1] == {"label": "AAPL", "value": "AAPL"}
