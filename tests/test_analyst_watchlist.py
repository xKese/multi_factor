"""Tests der Beobachtungsliste (Scope, Priorisierung, Symbolauflösung)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from app.core import persistence
from app.core.analyst import watchlist as wl
from app.core.config import Settings
from app.core.diagnostics import SEV_WARNING


def _scored():
    return pd.DataFrame(
        {
            "uid": ["SAP", "MBG", "SAN~sanofi", "SAN~bancosantander", "LOW"],
            "ticker": ["SAP", "MBG", "SAN", "SAN", "LOW"],
            "name": ["SAP SE", "Mercedes", "Sanofi", "Banco Santander", "Lowscore"],
            "zone_v2": ["KANDIDAT", "HALTEN", "KANDIDAT", "VERKAUFEN", "FILTER"],
            "composite_score": [90.0, 70.0, 85.0, 40.0, 20.0],
        }
    )


class _State:
    def resolve_portfolio(self):
        return pd.DataFrame({"uid": ["MBG", "GHOST"], "status": ["ok", "missing"]})


def test_portfolio_scope_unions_sources(monkeypatch):
    monkeypatch.setattr(persistence, "load_model_portfolio", lambda: pd.DataFrame({"uid": ["SAP", "MBG"]}))
    items, diags = wl.build_watchlist(Settings(), _scored(), _State())
    by_uid = {i.uid: i for i in items}
    assert set(by_uid) == {"SAP", "MBG", "SAN~sanofi"}
    assert by_uid["MBG"].reasons == {"portfolio", "modellportfolio"} and by_uid["MBG"].held
    assert by_uid["SAP"].reasons == {"modellportfolio", "kandidat"} and not by_uid["SAP"].held
    assert by_uid["SAN~sanofi"].tier == 2 and by_uid["MBG"].tier == 0
    assert diags == []


def test_universe_and_custom_scope(monkeypatch):
    monkeypatch.setattr(persistence, "load_model_portfolio", lambda: None)
    s = Settings()
    s.analyst_scope = "universe"
    items, _ = wl.build_watchlist(s, _scored(), None)
    assert len(items) == 5 and all(i.reasons == {"universum"} for i in items)

    s.analyst_scope = "custom"
    s.analyst_custom_tickers = ["sap", "SAN~sanofi", "SAN", "NOPE", ""]
    items, diags = wl.build_watchlist(s, _scored(), None)
    assert {i.uid for i in items} == {"SAP", "SAN~sanofi"}
    assert [d.code for d in diags] == ["custom_ticker_unknown"] * 2
    assert all(d.severity == SEV_WARNING for d in diags)

    items2, _ = wl.build_watchlist(s, _scored(), None, scope="custom", tickers=["MBG"])
    assert [i.uid for i in items2] == ["MBG"]
    with pytest.raises(ValueError):
        wl.build_watchlist(s, _scored(), None, scope="weird")
    assert wl.build_watchlist(s, pd.DataFrame(), None) == ([], [])


def test_prioritize_round_robin():
    a = wl.WatchItem("A", "A", "A", reasons={"portfolio"}, composite_score=50, last_checked_at=datetime(2026, 1, 2))
    b = wl.WatchItem("B", "B", "B", reasons={"portfolio"}, composite_score=90, last_checked_at=datetime(2026, 1, 1))
    c = wl.WatchItem("C", "C", "C", reasons={"portfolio"}, composite_score=10)
    d = wl.WatchItem("D", "D", "D", reasons={"kandidat"}, composite_score=99)
    e = wl.WatchItem("E", "E", "E", reasons={"universum"}, composite_score=99)
    order = [i.uid for i in wl.prioritize([a, b, c, d, e])]
    assert order == ["C", "B", "A", "D", "E"]
    assert [i.uid for i in wl.prioritize([a, b, c, d, e], max_n=2)] == ["C", "B"]


def test_apply_watch_states_and_resolve_symbols():
    items = [wl.WatchItem("SAP", "SAP", "SAP SE"), wl.WatchItem("MBG", "MBG", "Mercedes"), wl.WatchItem("X", "X", "X")]
    wl.apply_watch_states(items, {"SAP": {"last_checked_at": datetime(2026, 1, 1), "av_symbol": "SAP.DEX"}})
    assert items[0].av_symbol == "SAP.DEX" and items[0].last_checked_at == datetime(2026, 1, 1)

    class _Resolved:
        def __init__(self, sym):
            self.av_symbol = sym

    class _MD:
        calls = []

        @staticmethod
        def resolve_symbols(tickers, universe, settings, persist=True):
            _MD.calls.append(list(tickers))
            return {"MBG": _Resolved("MBG.DEX")}, ["X"]

    diags = wl.resolve_av_symbols(items, _scored(), Settings(), market_data=_MD)
    assert _MD.calls == [["MBG", "X"]]  # SAP war schon aufgelöst
    assert items[1].av_symbol == "MBG.DEX"
    assert [d.code for d in diags] == ["av_symbol_unresolved"] and diags[0].uid == "X"
    assert wl.resolve_av_symbols(items[:2], _scored(), Settings(), market_data=_MD) == []
