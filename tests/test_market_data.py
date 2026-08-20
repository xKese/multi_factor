"""Tests für Symbol-Auflösung, inkrementellen Cache und EUR-Preis-Panel."""

from __future__ import annotations

import importlib
from datetime import date

import pandas as pd
import pytest

from app.core.av_client import AlphaVantageError
from app.core.config import Settings


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Frische SQLite-DB je Test; liefert (market_data, av_store)."""

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app.core import av_store, market_data, persistence

    importlib.reload(persistence)
    importlib.reload(av_store)
    importlib.reload(market_data)
    return market_data, av_store


def _price_frame(values: dict[str, float]) -> pd.DataFrame:
    idx = pd.to_datetime(list(values.keys()))
    return pd.DataFrame(
        {"adj_close": list(values.values()), "close": list(values.values())},
        index=idx,
    )


class FakeAV:
    """Ersatz für ``av_client``: liefert vorgegebene Daten und zählt Calls."""

    AlphaVantageError = AlphaVantageError

    def __init__(self, *, search=None, prices=None, fx=None, y10=None, wti=None):
        self.search = search or {}
        self.prices = prices or {}
        self.fx = fx or {}
        self.y10 = y10 if y10 is not None else pd.Series(dtype=float)
        self.wti = wti if wti is not None else pd.Series(dtype=float)
        self.calls: list[tuple] = []

    def fetch_symbol_search(self, query, rpm=70):
        self.calls.append(("search", query))
        return self.search.get(query, [])

    def fetch_daily_adjusted(self, symbol, outputsize="full", rpm=70):
        self.calls.append(("daily", symbol, outputsize))
        if symbol not in self.prices:
            raise AlphaVantageError(f"unbekanntes Symbol {symbol}")
        return self.prices[symbol].copy()

    def fetch_fx_daily(self, from_symbol, to_symbol="EUR", outputsize="full", rpm=70):
        self.calls.append(("fx", from_symbol))
        if from_symbol not in self.fx:
            raise AlphaVantageError(f"kein FX für {from_symbol}")
        return self.fx[from_symbol].copy()

    def fetch_treasury_yield_10y(self, rpm=70):
        self.calls.append(("y10",))
        return self.y10.copy()

    def fetch_wti(self, rpm=70):
        self.calls.append(("wti",))
        return self.wti.copy()


def _match(symbol, currency, region="United States", score=1.0, type_="Equity"):
    return {
        "symbol": symbol,
        "name": symbol,
        "type": type_,
        "region": region,
        "currency": currency,
        "match_score": score,
    }


DAYS = ["2024-01-02", "2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"]


def _fake_full(monkeypatch, market_data, tickers=("AAA",)):
    fx_usd = pd.Series(
        [0.90, 0.90, 0.90, 0.90, 0.90], index=pd.to_datetime(DAYS)
    )
    fake = FakeAV(
        search={t: [_match(t, "USD")] for t in tickers},
        prices={
            "ACWI": _price_frame(dict(zip(DAYS, [100, 101, 102, 103, 104]))),
            **{
                t: _price_frame(dict(zip(DAYS, [50, 51, 52, 53, 54])))
                for t in tickers
            },
        },
        fx={"USD": fx_usd},
        y10=pd.Series([4.0] * 5, index=pd.to_datetime(DAYS)),
        wti=pd.Series([70.0] * 5, index=pd.to_datetime(DAYS)),
    )
    monkeypatch.setattr(market_data, "av_client", fake)
    return fake


# ── Symbol-Auflösung ───────────────────────────────────────────────────────


def test_resolve_prefers_stored_mapping(env, monkeypatch):
    market_data, av_store = env
    av_store.save_av_mapping("SAP", "SAP.DEX", "EUR", confirmed=True)
    fake = FakeAV()
    monkeypatch.setattr(market_data, "av_client", fake)

    resolved, unresolved = market_data.resolve_symbols(["SAP"], None, Settings())

    assert unresolved == []
    assert resolved["SAP"].av_symbol == "SAP.DEX"
    assert resolved["SAP"].currency == "EUR"
    assert resolved["SAP"].source == "mapping"
    assert fake.calls == []  # kein API-Call nötig


def test_resolve_suffix_heuristic_validated_by_search(env, monkeypatch):
    """Yahoo-/Koyfin-Suffix .DE wird zum AV-Kandidaten .DEX und per
    SYMBOL_SEARCH (exakter Treffer) bestätigt; Währung aus dem Treffer."""

    market_data, av_store = env
    fake = FakeAV(search={"SAP.DEX": [_match("SAP.DEX", "EUR", "XETRA", 0.7)]})
    monkeypatch.setattr(market_data, "av_client", fake)

    resolved, _ = market_data.resolve_symbols(["SAP.DE"], None, Settings())

    assert resolved["SAP.DE"].av_symbol == "SAP.DEX"
    assert resolved["SAP.DE"].currency == "EUR"
    assert resolved["SAP.DE"].source == "heuristik"
    # Auflösung wurde persistiert → zweiter Lauf ohne Suche.
    fake.calls.clear()
    resolved2, _ = market_data.resolve_symbols(["SAP.DE"], None, Settings())
    assert resolved2["SAP.DE"].source == "mapping"
    assert fake.calls == []


def test_resolve_share_class(env, monkeypatch):
    market_data, _ = env
    fake = FakeAV(search={"BRK-B": [_match("BRK-B", "USD")]})
    monkeypatch.setattr(market_data, "av_client", fake)

    resolved, _ = market_data.resolve_symbols(["BRKB"], None, Settings())
    assert resolved["BRKB"].av_symbol == "BRK-B"


def test_resolve_non_us_hint_avoids_otc_double_listing(env, monkeypatch):
    """Ohne exakten Treffer und mit Non-US-Region-Hinweis wird das
    US-(OTC-)Doppellisting nachrangig behandelt."""

    market_data, _ = env
    universe = pd.DataFrame(
        [{"ticker": "XYZ", "name": "Xyz AG", "region": "Germany"}]
    )
    fake = FakeAV(
        search={
            "XYZ": [
                _match("XYZOF", "USD", "United States", 1.0),
                _match("XYZ.DEX", "EUR", "XETRA", 0.8),
            ]
        }
    )
    monkeypatch.setattr(market_data, "av_client", fake)

    resolved, _ = market_data.resolve_symbols(["XYZ"], universe, Settings())
    assert resolved["XYZ"].av_symbol == "XYZ.DEX"
    assert resolved["XYZ"].source == "suche"


def test_resolve_unresolvable_ticker_reported(env, monkeypatch):
    market_data, _ = env
    fake = FakeAV(search={})
    monkeypatch.setattr(market_data, "av_client", fake)

    resolved, unresolved = market_data.resolve_symbols(["NOPE"], None, Settings())
    assert resolved == {}
    assert unresolved == ["NOPE"]


# ── Cache-Logik (Pflichttest) ──────────────────────────────────────────────


def test_second_update_run_makes_no_api_calls(env, monkeypatch):
    """Pflichttest: Lauf 1 lädt und cached, Lauf 2 am selben Tag macht für
    bereits geladene Tage keinerlei API-Calls."""

    market_data, _ = env
    fake = _fake_full(monkeypatch, market_data)
    settings = Settings()
    asof = date(2024, 1, 8)
    today = date(2024, 1, 10)

    first = market_data.update_cache(["AAA"], None, settings, asof, today)
    assert first["api_calls"] > 0
    assert first["fehler"] == []
    assert "AAA" in first["aktualisiert"]

    fake.calls.clear()
    second = market_data.update_cache(["AAA"], None, settings, asof, today)
    assert second["api_calls"] == 0
    assert fake.calls == []
    assert second["aktualisiert"] == []


def test_update_skips_when_cache_covers_asof(env, monkeypatch):
    """Ein Stichtag, den der Cache schon abdeckt, löst auch an einem
    späteren Tag keinen erneuten Abruf aus."""

    market_data, _ = env
    fake = _fake_full(monkeypatch, market_data)
    settings = Settings()
    market_data.update_cache(["AAA"], None, settings, date(2024, 1, 8), date(2024, 1, 8))

    fake.calls.clear()
    # Neuer Tag, aber asof weiterhin durch last_refreshed (2024-01-08) gedeckt.
    result = market_data.update_cache(
        ["AAA"], None, settings, date(2024, 1, 5), date(2024, 2, 1)
    )
    assert not any(c[0] in ("daily", "fx", "y10", "wti") for c in fake.calls)
    assert result["api_calls"] == 0


def test_update_refetches_full_on_retroactive_adjustment(env, monkeypatch):
    """Weicht der Adjusted Close am Overlap-Tag ab (Split/Dividende), wird
    die volle Historie neu geladen."""

    market_data, av_store = env
    fake = _fake_full(monkeypatch, market_data)
    settings = Settings()
    market_data.update_cache(["AAA"], None, settings, date(2024, 1, 8), date(2024, 1, 8))

    # Historie rückwirkend halbiert (2:1-Split) plus ein neuer Tag.
    new_days = DAYS + ["2024-01-09"]
    fake.prices["AAA"] = _price_frame(
        dict(zip(new_days, [25.0, 25.5, 26.0, 26.5, 27.0, 27.5]))
    )
    fake.calls.clear()
    market_data.update_cache(["AAA"], None, settings, date(2024, 1, 9), date(2024, 1, 9))

    daily_calls = [c for c in fake.calls if c[0] == "daily"]
    assert ("daily", "AAA", "compact") in daily_calls
    assert ("daily", "AAA", "full") in daily_calls
    cached = av_store.load_prices("AAA")
    assert cached.loc[pd.Timestamp("2024-01-02"), "adj_close"] == pytest.approx(25.0)


def test_update_single_symbol_failure_does_not_stop_run(env, monkeypatch):
    market_data, _ = env
    fake = _fake_full(monkeypatch, market_data, tickers=("AAA", "BBB"))
    del fake.prices["BBB"]
    settings = Settings()

    result = market_data.update_cache(
        ["AAA", "BBB"], None, settings, date(2024, 1, 8), date(2024, 1, 8)
    )
    assert "AAA" in result["aktualisiert"]
    assert any("BBB" in f for f in result["fehler"])


# ── EUR-Panel (Pflichttest Währungsumrechnung) ─────────────────────────────


def _seed_panel(av_store, *, stock_days, stock_prices, fx_path):
    """Cache mit Benchmark (USD), einem USD-Titel und USD→EUR-FX füllen."""

    from datetime import datetime

    bm = _price_frame(dict(zip(DAYS, [100, 101, 102, 103, 104])))
    av_store.save_prices("ACWI", bm)
    av_store.set_symbol_meta(
        "ACWI", "USD", "benchmark", date(2024, 1, 8), datetime(2024, 1, 8, 22, 0)
    )
    av_store.save_av_mapping("USDCO", "USDCO", "USD")
    av_store.save_prices("USDCO", _price_frame(dict(zip(stock_days, stock_prices))))
    av_store.set_symbol_meta(
        "USDCO", "USD", "aktie", date(2024, 1, 8), datetime(2024, 1, 8, 22, 5)
    )
    fx = pd.DataFrame({"adj_close": pd.Series(fx_path, index=pd.to_datetime(DAYS))})
    av_store.save_prices("FX:USDEUR", fx)


def test_eur_conversion_known_fx_path(env):
    """Pflichttest: USD-Titel 100→110 USD bei FX 0,90→0,85 EUR/USD ergibt
    die EUR-Rendite 1,10·0,85/0,90 − 1 ≈ 3,889 %."""

    market_data, av_store = env
    _seed_panel(
        av_store,
        stock_days=[DAYS[0], DAYS[1]],
        stock_prices=[100.0, 110.0],
        fx_path=[0.90, 0.85, 0.85, 0.85, 0.85],
    )

    panel = market_data.load_price_panel(["USDCO"], Settings(), date(2024, 1, 8))

    series = panel.prices_eur["USDCO"]
    ret = series.loc[pd.Timestamp("2024-01-03")] / series.loc[
        pd.Timestamp("2024-01-02")
    ] - 1.0
    assert ret == pytest.approx(1.10 * 0.85 / 0.90 - 1.0, abs=1e-12)
    # Benchmark ebenfalls in EUR: 100 USD × 0,90 = 90 EUR am ersten Tag.
    assert panel.benchmark.loc[pd.Timestamp("2024-01-02")] == pytest.approx(90.0)


def test_panel_ffill_limit_and_gap_flag(env):
    """Fehlende Tage werden max. 3 Tage forward-gefillt; längere Lücken
    bleiben NaN und werden als Datenlücke gezählt."""

    market_data, av_store = env
    # Kurs nur am ersten Tag → 4 fehlende Kalendertage, ffill deckt 3.
    _seed_panel(
        av_store,
        stock_days=[DAYS[0]],
        stock_prices=[100.0],
        fx_path=[0.90] * 5,
    )

    panel = market_data.load_price_panel(["USDCO"], Settings(), date(2024, 1, 8))

    series = panel.prices_eur["USDCO"]
    assert series.notna().sum() == 4  # Tag 1 + 3 ffill-Tage
    assert panel.quality.gaps["USDCO"] == 1
    assert panel.quality.last_price["USDCO"] == date(2024, 1, 5)


def test_panel_reports_unresolved_and_missing_cache(env):
    market_data, av_store = env
    _seed_panel(
        av_store,
        stock_days=DAYS,
        stock_prices=[100, 101, 102, 103, 104],
        fx_path=[0.90] * 5,
    )
    av_store.save_av_mapping("EMPTY", "EMPTY", "USD")

    panel = market_data.load_price_panel(
        ["USDCO", "EMPTY", "UNKNOWN"], Settings(), date(2024, 1, 8)
    )

    assert panel.quality.unresolved == ["UNKNOWN"]
    assert panel.quality.missing_cache == ["EMPTY"]
    assert list(panel.prices_eur.columns) == ["USDCO"]


def test_panel_without_benchmark_raises_german_hint(env):
    market_data, _ = env
    with pytest.raises(ValueError) as err:
        market_data.load_price_panel(["USDCO"], Settings(), date(2024, 1, 8))
    assert "update" in str(err.value)


def test_load_macro_inverts_usdeur(env, monkeypatch):
    market_data, av_store = env
    idx = pd.to_datetime(DAYS)
    av_store.save_prices("MACRO:Y10", pd.DataFrame({"adj_close": pd.Series([4.0] * 5, index=idx)}))
    av_store.save_prices("MACRO:WTI", pd.DataFrame({"adj_close": pd.Series([70.0] * 5, index=idx)}))
    av_store.save_prices("FX:USDEUR", pd.DataFrame({"adj_close": pd.Series([0.8] * 5, index=idx)}))

    macro = market_data.load_macro(date(2024, 1, 8))
    assert macro["eurusd"].iloc[0] == pytest.approx(1.25)
    assert macro["y10"].iloc[0] == pytest.approx(4.0)
