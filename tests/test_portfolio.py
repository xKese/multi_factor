"""Tests für den M&S-Portfolio-Import und die Handlungs-Flag-Logik."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.portfolio import (
    FLAG_BEARISH,
    FLAG_DEATH,
    FLAG_FILTER,
    FLAG_NEW,
    FLAG_SELL,
    FLAG_TIRED,
    build_flags,
    load_portfolio_csv,
)
from app.core.state import AppState


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_portfolio_sample.csv"


# ── Loader ─────────────────────────────────────────────────────────────────

def test_fixture_parsed_with_group_headers_dedupe_and_case():
    df = load_portfolio_csv(FIXTURE.read_bytes())
    # Gruppen-Kopfzeilen ("MSCI World", "Watch") raus, Duplikat (MSFT) raus,
    # lowercase (nvda) normalisiert, Datei-Reihenfolge erhalten.
    assert list(df["ticker"]) == ["MSFT", "NVDA", "ORCL", "ZZZZ"]
    assert df.loc[df["ticker"] == "ZZZZ", "name"].iloc[0] == "Unknown Corp"


def test_semicolon_variant():
    raw = FIXTURE.read_text().replace(",", ";")
    df = load_portfolio_csv(raw.encode())
    assert list(df["ticker"]) == ["MSFT", "NVDA", "ORCL", "ZZZZ"]


def test_ticker_header_detected_at_any_position():
    df = load_portfolio_csv(b"Name,Ticker\nMicrosoft,MSFT\nOracle,ORCL\n")
    assert list(df["ticker"]) == ["MSFT", "ORCL"]
    assert df["name"].tolist() == ["Microsoft", "Oracle"]


def test_no_ticker_header_falls_back_to_first_column():
    df = load_portfolio_csv(b"Wertpapier,Bezeichnung\nAAA,Alpha\nBBB,Beta\n")
    assert list(df["ticker"]) == ["AAA", "BBB"]
    assert df["name"].tolist() == ["Alpha", "Beta"]


def test_single_column_file():
    df = load_portfolio_csv(b"Ticker\nAAA\nBBB\n")
    assert list(df["ticker"]) == ["AAA", "BBB"]
    assert df["name"].tolist() == ["", ""]


def test_empty_file_raises():
    with pytest.raises(ValueError):
        load_portfolio_csv(b"Ticker\n")


# ── Optionale Gewichtsspalte ───────────────────────────────────────────────

WEIGHTS_FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "koyfin_portfolio_weights_sample.csv"
)


def test_weight_column_parsed_normalized_and_deduped():
    """Prozent-Skala (Summe ≈ 100) wird zu Dezimalanteilen; Gruppen-Kopf und
    Duplikat fallen raus, danach wird auf Summe 1,0 renormalisiert."""

    df = load_portfolio_csv(WEIGHTS_FIXTURE.read_bytes())

    assert list(df["ticker"]) == ["AAPL", "MSFT", "SAP"]
    assert df["weight"].sum() == pytest.approx(1.0)
    weights = dict(zip(df["ticker"], df["weight"]))
    assert weights["AAPL"] == pytest.approx(0.25)
    assert weights["SAP"] == pytest.approx(0.40)


def test_weight_column_decimal_fractions_kept():
    raw = b"Ticker;Weight\nAAA;0,6\nBBB;0,4\n"
    df = load_portfolio_csv(raw)
    assert dict(zip(df["ticker"], df["weight"])) == pytest.approx(
        {"AAA": 0.6, "BBB": 0.4}
    )


def test_missing_weight_column_yields_no_weight():
    df = load_portfolio_csv(FIXTURE.read_bytes())
    assert "weight" not in df.columns


def test_state_equal_weight_fallback():
    state = AppState()
    state.set_ms_portfolio(pd.DataFrame({"ticker": ["A", "B", "C", "D"]}))
    weights = state.portfolio_weights()
    assert weights == pytest.approx({t: 0.25 for t in ["A", "B", "C", "D"]})


def test_state_uses_imported_weights():
    state = AppState()
    state.set_ms_portfolio(
        pd.DataFrame({"ticker": ["A", "B"], "weight": [0.7, 0.3]})
    )
    assert state.portfolio_weights() == pytest.approx({"A": 0.7, "B": 0.3})


def test_weights_roundtrip_through_db(tmp_path, monkeypatch):
    """Gewichte überleben save_ms_portfolio → load_ms_portfolio, inkl.
    ALTER-Migration einer Bestandstabelle ohne weight-Spalte."""

    import importlib

    from sqlalchemy import text

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app.core import persistence

    importlib.reload(persistence)

    # Bestandstabelle im alten Schema (ohne weight) anlegen.
    engine = persistence.get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE ms_portfolio ("
                "position INTEGER PRIMARY KEY, ticker TEXT NOT NULL, "
                "name TEXT, imported_at TIMESTAMP NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)"
            )
        )

    df = pd.DataFrame(
        {"ticker": ["AAA", "BBB"], "name": ["A", "B"], "weight": [0.6, 0.4]}
    )
    assert persistence.save_ms_portfolio(df) == 2

    loaded = persistence.load_ms_portfolio()
    assert dict(zip(loaded["ticker"], loaded["weight"])) == pytest.approx(
        {"AAA": 0.6, "BBB": 0.4}
    )

    state = AppState()
    state.set_ms_portfolio(loaded)
    assert state.portfolio_weights() == pytest.approx({"AAA": 0.6, "BBB": 0.4})


# ── Handlungs-Flags ────────────────────────────────────────────────────────

def _scored_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            # Sauber — darf nicht im Ergebnis auftauchen.
            {"ticker": "OK", "recommendation": "BUY", "sma_signal": "✓ GOLDEN CROSS",
             "trend_phase": "Etabliert bullish", "total_score": 75.0, "is_new": False},
            {"ticker": "SL", "recommendation": "SELL", "sma_signal": "✓ GOLDEN CROSS",
             "trend_phase": "Etabliert bullish", "total_score": 45.0, "is_new": False},
            {"ticker": "FF", "recommendation": "Filter nicht bestanden",
             "sma_signal": "● Kurs > SMA-200", "trend_phase": "Etabliert bullish",
             "total_score": 55.0, "is_new": False},
            {"ticker": "DC", "recommendation": "HOLD", "sma_signal": "⚠ DEATH CROSS",
             "trend_phase": "Etabliert bearish", "total_score": 50.0, "is_new": False},
            {"ticker": "BE", "recommendation": "HOLD", "sma_signal": "▼ Kurs < SMA-200",
             "trend_phase": "Neutral", "total_score": 52.0, "is_new": False},
            {"ticker": "TI", "recommendation": "HOLD", "sma_signal": "✓ GOLDEN CROSS",
             "trend_phase": "Ermüdet bullish", "total_score": 60.0, "is_new": False},
            {"ticker": "NW", "recommendation": "BUY", "sma_signal": "✓ GOLDEN CROSS",
             "trend_phase": "Etabliert bullish", "total_score": 70.0, "is_new": True},
        ]
    )


def test_flags_detect_each_condition_and_sort_by_severity():
    flags = build_flags(_scored_frame())
    assert "OK" not in set(flags["ticker"])
    assert list(flags["ticker"]) == ["SL", "FF", "DC", "BE", "TI", "NW"]
    per_ticker = dict(zip(flags["ticker"], flags["flags"]))
    assert per_ticker["SL"] == [FLAG_SELL]
    assert per_ticker["FF"] == [FLAG_FILTER]
    assert per_ticker["DC"] == [FLAG_DEATH]
    assert per_ticker["BE"] == [FLAG_BEARISH]
    assert per_ticker["TI"] == [FLAG_TIRED]
    assert per_ticker["NW"] == [FLAG_NEW]


def test_multi_flag_row_aggregates_with_min_severity():
    df = pd.DataFrame(
        [
            {"ticker": "X", "recommendation": "SELL", "sma_signal": "⚠ DEATH CROSS",
             "trend_phase": "Etabliert bearish", "total_score": 30.0, "is_new": True},
        ]
    )
    flags = build_flags(df)
    assert set(flags.iloc[0]["flags"]) == {FLAG_SELL, FLAG_DEATH, FLAG_NEW}
    assert flags.iloc[0]["severity"] == 0


def test_all_clean_frame_yields_empty():
    df = _scored_frame().iloc[[0]]
    assert build_flags(df).empty


# ── Persistenz fail-open ───────────────────────────────────────────────────

def test_persistence_fail_open_without_engine(monkeypatch):
    import app.core.persistence as persistence

    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert persistence.load_ms_portfolio() is None
    with pytest.raises(RuntimeError):
        persistence.save_ms_portfolio(pd.DataFrame({"ticker": ["AAA"]}))


# ── State ──────────────────────────────────────────────────────────────────

def test_set_ms_portfolio_sets_list_names_and_date():
    state = AppState()
    df = pd.DataFrame({"ticker": ["AAA", "BBB"], "name": ["Alpha", ""]})
    state.set_ms_portfolio(df, imported_at="2026-07-14")
    assert state.ms_portfolio == ["AAA", "BBB"]
    assert state.ms_portfolio_names == {"AAA": "Alpha", "BBB": ""}
    assert state.ms_portfolio_imported_at == "2026-07-14"


def test_load_from_db_uses_portfolio_when_present(monkeypatch):
    import app.core.persistence as persistence

    default = AppState().ms_portfolio
    monkeypatch.setattr(persistence, "load_settings", lambda: None)
    monkeypatch.setattr(persistence, "load_universe", lambda: None)

    # Ohne persistiertes Portfolio bleibt der hartkodierte Default.
    monkeypatch.setattr(persistence, "load_ms_portfolio", lambda: None)
    state = AppState()
    state.load_from_db()
    assert state.ms_portfolio == default

    # Mit persistiertem Portfolio wird ersetzt.
    stored = pd.DataFrame({"ticker": ["AAA"], "name": ["Alpha"],
                           "imported_at": ["2026-07-01"]})
    monkeypatch.setattr(persistence, "load_ms_portfolio", lambda: stored)
    state = AppState()
    state.load_from_db()
    assert state.ms_portfolio == ["AAA"]
    assert state.ms_portfolio_imported_at == "2026-07-01"


def test_my_portfolio_removed():
    assert not hasattr(AppState(), "my_portfolio")
