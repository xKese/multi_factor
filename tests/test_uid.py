"""Tests für die Duplikat-Ticker-Behandlung (uid).

Szenario: Koyfin exportiert Ticker ohne Börsensuffix — Sanofi und Banco
Santander sind beide "SAN". Die uid macht jede Zeile adressierbar, ohne
Bestandsdaten zu invalidieren (eindeutige Ticker: uid == ticker).
"""

from __future__ import annotations

from datetime import date

import pandas as pd

from app.core import persistence, ticker_mapping
from app.core.uid import (
    assign_uids,
    base_ticker,
    duplicate_ticker_info,
    row_by_uid,
    rows_by_uid_index,
    slugify_name,
)


def _dupe_universe() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["SAN", "SAN", "MSFT"],
            "name": ["Sanofi", "Banco Santander", "Microsoft"],
            "sector": ["Health Care", "Financials", "Information Technology"],
            "region": ["EU", "EU", "US"],
        }
    )


def test_assign_uids_unique_tickers_keep_ticker():
    df = assign_uids(pd.DataFrame({"ticker": ["AAPL", "MSFT"], "name": ["A", "M"]}))
    assert (df["uid"] == df["ticker"]).all()


def test_assign_uids_collision_gets_name_slug():
    df = assign_uids(_dupe_universe())
    assert df["uid"].tolist() == ["SAN~sanofi", "SAN~bancosantander", "MSFT"]
    assert df["uid"].is_unique


def test_assign_uids_slug_collision_gets_suffix():
    df = assign_uids(
        pd.DataFrame({"ticker": ["SAN", "SAN"], "name": ["Sanofi", "Sanofi"]})
    )
    assert df["uid"].tolist() == ["SAN~sanofi", "SAN~sanofi-2"]


def test_slugify_and_base_ticker():
    assert slugify_name("Banco Santander, S.A.") == "bancosantandersa"
    assert base_ticker("SAN~sanofi") == "SAN"
    assert base_ticker("MSFT") == "MSFT"


def test_row_by_uid_with_ticker_fallback():
    df = assign_uids(_dupe_universe())
    assert row_by_uid(df, "SAN~bancosantander")["name"] == "Banco Santander"
    # Alt-Link mit bloßem Ticker: erste Zeile (bisheriges Verhalten).
    assert row_by_uid(df, "SAN")["name"] == "Sanofi"
    assert row_by_uid(df, "UNBEKANNT") is None
    assert len(rows_by_uid_index(df, "SAN~sanofi")) == 1


def test_duplicate_ticker_info():
    df = assign_uids(_dupe_universe())
    dupes = duplicate_ticker_info(df)
    assert len(dupes) == 1
    ticker, pairs = dupes[0]
    assert ticker == "SAN"
    assert ("SAN~sanofi", "Sanofi") in pairs
    assert ("SAN~bancosantander", "Banco Santander") in pairs


def test_loader_assigns_uids_on_duplicate_rows():
    from pathlib import Path

    from app.core.data_loader import load_koyfin_csv

    fixture = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"
    raw = fixture.read_text(encoding="utf-8")
    lines = raw.splitlines()
    msft = next(line for line in lines if line.startswith("MSFT;"))
    lines.append(msft.replace("MSFT;Microsoft;", "MSFT;Mikrosoft Zwei;", 1))
    df = load_koyfin_csv("\n".join(lines).encode("utf-8"))

    assert df["uid"].is_unique
    dupes = df.loc[df["ticker"] == "MSFT", "uid"].tolist()
    assert sorted(dupes) == ["MSFT~microsoft", "MSFT~mikrosoftzwei"]
    # Alle übrigen Ticker unverändert adressierbar.
    assert (df.loc[df["ticker"] != "MSFT", "uid"] == df.loc[df["ticker"] != "MSFT", "ticker"]).all()


# ── Portfolio-Auflösung ─────────────────────────────────────────────────────


def _state_with_universe():
    from app.core.state import AppState

    state = AppState()
    state.scored = assign_uids(_dupe_universe())
    return state


def test_resolve_portfolio_by_name_and_ambiguous():
    state = _state_with_universe()
    state.set_ms_portfolio(
        pd.DataFrame(
            {
                "ticker": ["SAN", "SAN", "MSFT", "FEHLT"],
                "name": ["Sanofi", "", "Microsoft", ""],
            }
        )
    )
    resolved = state.resolve_portfolio()
    by_pos = resolved.to_dict("records")
    # Namens-Match löst die Kollision auf.
    assert by_pos[0]["status"] == "ok" and by_pos[0]["uid"] == "SAN~sanofi"
    # Ohne Namen bleibt die Kollision mehrdeutig — kein Doppel-Match.
    assert by_pos[1]["status"] == "ambiguous"
    assert by_pos[2]["status"] == "ok" and by_pos[2]["uid"] == "MSFT"
    assert by_pos[3]["status"] == "missing"


def test_portfolio_weights_keyed_by_uid():
    state = _state_with_universe()
    state.set_ms_portfolio(
        pd.DataFrame(
            {
                "ticker": ["SAN", "SAN", "MSFT"],
                "name": ["Sanofi", "Banco Santander", "Microsoft"],
                "weight": [0.5, 0.3, 0.2],
            }
        )
    )
    weights = state.portfolio_weights()
    assert set(weights) == {"SAN~sanofi", "SAN~bancosantander", "MSFT"}
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert abs(weights["SAN~sanofi"] - 0.5) < 1e-9


# ── Signal-Events je uid ────────────────────────────────────────────────────


def test_signal_events_keyed_by_uid():
    from app.core.signal_events import derive_signal_events

    current = assign_uids(_dupe_universe())
    current["last_price"] = [110.0, 80.0, 120.0]
    current["sma_50"] = [105.0, 90.0, 110.0]
    current["sma_200"] = [100.0, 100.0, 100.0]

    history = pd.DataFrame(
        {
            "snapshot_date": [date(2026, 8, 20)] * 2,
            "ticker": ["SAN~sanofi", "SAN~bancosantander"],
            "momentum": ["Golden Cross", "Golden Cross"],
        }
    )
    events = derive_signal_events(current, history, date(2026, 8, 27))
    assert len(events) == 3  # beide SAN getrennt + MSFT
    by_uid = events.set_index("uid")
    assert not bool(by_uid.loc["SAN~sanofi", "is_new"])  # weiter Golden Cross
    assert bool(by_uid.loc["SAN~bancosantander", "is_new"])  # → Death Cross
    assert by_uid.loc["SAN~bancosantander", "momentum"] == "Death Cross"
    # Anzeige-Ticker bleibt das reine Symbol.
    assert by_uid.loc["SAN~sanofi", "ticker"] == "SAN"


# ── Mapping-Schlüssel je uid ────────────────────────────────────────────────


def test_ticker_mapping_collision_uid_requires_confirmation(monkeypatch):
    stored: dict[str, str] = {}
    monkeypatch.setattr(persistence, "load_ticker_mapping", stored.get)

    # Kollisions-uid ohne gespeichertes Mapping: nie heuristisch raten.
    assert ticker_mapping.resolve("SAN~sanofi", region="US") is None
    # Gespeicherte Mappings koexistieren je uid.
    stored["SAN~sanofi"] = "SAN.PA"
    stored["SAN~bancosantander"] = "SAN.MC"
    assert ticker_mapping.resolve("SAN~sanofi") == "SAN.PA"
    assert ticker_mapping.resolve("SAN~bancosantander") == "SAN.MC"
    # Eindeutige Ticker: unverändertes Verhalten.
    assert ticker_mapping.resolve("AAPL", region="US") == "AAPL"


def test_peers_allow_other_collision_ticker():
    from app.core.peers import compute_peers

    df = assign_uids(_dupe_universe())
    for col in (
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
    ):
        df[col] = [50.0, 60.0, 70.0]
    df["total_score"] = [55.0, 65.0, 75.0]
    df["industry"] = ["Pharma", "Banks", "Software"]

    peers = compute_peers(df, "SAN~sanofi", n=2)
    peer_uids = set(peers["uid"])
    assert "SAN~sanofi" not in peer_uids
    assert "SAN~bancosantander" in peer_uids


# ── AV-Symbol-Suche mit Namens-Ranking ─────────────────────────────────────


def test_pick_search_match_uses_name_hint():
    from app.core.market_data import _pick_search_match

    results = [
        {
            "symbol": "SAN.PAR",
            "name": "Sanofi",
            "type": "Equity",
            "region": "Paris",
            "match_score": 0.7,
            "currency": "EUR",
        },
        {
            "symbol": "SAN.MCE",
            "name": "Banco Santander S.A.",
            "type": "Equity",
            "region": "Madrid",
            "match_score": 0.75,
            "currency": "EUR",
        },
    ]
    # Ohne Namens-Hint gewinnt der höhere match_score (Santander) …
    assert _pick_search_match(results, "non_us")["symbol"] == "SAN.MCE"
    # … mit Namens-Hint deterministisch die richtige Firma.
    assert _pick_search_match(results, "non_us", "Sanofi")["symbol"] == "SAN.PAR"
    assert (
        _pick_search_match(results, "non_us", "Banco Santander")["symbol"]
        == "SAN.MCE"
    )
