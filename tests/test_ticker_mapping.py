"""Tests für die Koyfin→Yahoo-Ticker-Auflösung."""

from __future__ import annotations

from app.core import persistence, ticker_mapping


def test_us_ticker_passes_through(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    # Koyfin ist bei der Regions-Bezeichnung nicht einheitlich — alle
    # US-Varianten müssen ohne Bestätigungs-Modal durchgehen.
    assert ticker_mapping.resolve("AAPL", region="USA") == "AAPL"
    assert ticker_mapping.resolve("aapl", region="United States") == "AAPL"
    assert ticker_mapping.resolve("AAPL", region="United States of America") == "AAPL"
    assert ticker_mapping.resolve("AAPL", region="Americas") == "AAPL"
    assert ticker_mapping.resolve("AAPL", region="North America") == "AAPL"
    assert ticker_mapping.resolve("AAPL", region="US") == "AAPL"


def test_unknown_region_passes_through_optimistically(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    # Unbekannte/leere Region: optimistisch durchreichen (US-Annahme) —
    # Korrektur jederzeit über den „Börsen-Ticker ändern“-Escape-Hatch.
    assert ticker_mapping.resolve("MSFT", region=None) == "MSFT"
    assert ticker_mapping.resolve("MSFT", region="") == "MSFT"
    assert ticker_mapping.resolve("XYZ", region="Sonstige") == "XYZ"


def test_us_share_class_translated(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    assert ticker_mapping.resolve("BRKB", region="USA") == "BRK-B"
    assert ticker_mapping.resolve("BRKB", region="United States") == "BRK-B"


def test_non_us_ticker_needs_confirmation(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    # Klar nicht-US: Koyfin-Ticker ohne Börsen-Endung braucht den
    # Yahoo-Suffix → keine automatische Auflösung, UI fragt nach.
    assert ticker_mapping.resolve("MBG", region="Deutschland") is None
    assert ticker_mapping.resolve("MBG", region="Germany") is None
    assert ticker_mapping.resolve("MBG", region="Europe") is None
    # Kanada braucht .TO, obwohl Nordamerika — non_us gewinnt vor us.
    assert ticker_mapping.resolve("RY", region="Canada") is None


def test_classify_region():
    assert ticker_mapping.classify_region("United States of America") == "us"
    assert ticker_mapping.classify_region("AMERICAS") == "us"
    assert ticker_mapping.classify_region("us") == "us"
    assert ticker_mapping.classify_region("Germany") == "non_us"
    assert ticker_mapping.classify_region("Canada") == "non_us"
    assert ticker_mapping.classify_region(None) == "unknown"
    assert ticker_mapping.classify_region("") == "unknown"
    assert ticker_mapping.classify_region("Sonstige") == "unknown"


def test_stored_mapping_wins(monkeypatch):
    monkeypatch.setattr(
        persistence, "load_ticker_mapping", lambda t: "MBG.F" if t == "MBG" else None
    )
    assert ticker_mapping.resolve("MBG", region="Deutschland") == "MBG.F"


def test_already_suffixed_ticker_passes(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    assert ticker_mapping.resolve("MBG.DE", region="Deutschland") == "MBG.DE"
    assert ticker_mapping.resolve("0700.HK", region="China") == "0700.HK"


def test_invalid_ticker_rejected(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    assert ticker_mapping.resolve("", region="USA") is None
    assert ticker_mapping.resolve("BAD TICKER!", region="USA") is None
    assert ticker_mapping.resolve(None, region="USA") is None


def test_rank_suggestions_prefers_name_and_region():
    results = [
        {"symbol": "MBG.MX", "name": "Mercedes-Benz Group AG", "region": "Mexico"},
        {"symbol": "MBG.F", "name": "Mercedes-Benz Group AG", "region": "Frankfurt"},
        {"symbol": "XYZ", "name": "Другое", "region": "Frankfurt"},
    ]
    ranked = ticker_mapping.rank_suggestions(
        results, name="Mercedes-Benz Group AG", region="Frankfurt"
    )
    assert ranked[0]["symbol"] == "MBG.F"


def test_rank_suggestions_empty():
    assert ticker_mapping.rank_suggestions([], name="x", region="y") == []
