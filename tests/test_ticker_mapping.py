"""Tests für die Koyfin→Yahoo-Ticker-Auflösung."""

from __future__ import annotations

from app.core import persistence, ticker_mapping


def test_us_ticker_passes_through(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    assert ticker_mapping.resolve("AAPL", region="USA") == "AAPL"
    assert ticker_mapping.resolve("aapl", region="United States") == "AAPL"


def test_us_share_class_translated(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    assert ticker_mapping.resolve("BRKB", region="USA") == "BRK-B"


def test_european_ticker_needs_confirmation(monkeypatch):
    monkeypatch.setattr(persistence, "load_ticker_mapping", lambda t: None)
    # Koyfin-Ticker ohne Börsen-Endung, keine US-Region → keine automatische
    # Auflösung; die UI muss den Nutzer bestätigen lassen.
    assert ticker_mapping.resolve("MBG", region="Deutschland") is None
    assert ticker_mapping.resolve("MBG", region=None) is None


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
