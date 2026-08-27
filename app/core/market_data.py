"""Marktdaten-Orchestrierung für das Risiko-&-Benchmark-Modul.

Verantwortlich für drei Dinge:

1. **Symbol-Auflösung** App-Ticker → Alpha-Vantage-Symbol (``resolve_symbols``):
   gespeichertes Mapping → Suffix-/Share-Class-Heuristik → SYMBOL_SEARCH.
   Jede Auflösung wird per SYMBOL_SEARCH validiert (liefert zugleich die
   Handelswährung) und in ``av_ticker_mappings`` persistiert, sodass sie API-
   Calls nur einmal kostet. Nicht auflösbare Ticker landen im
   Datenqualitätsteil des Reports, nie in einer Exception.
2. **Inkrementelles Cache-Update** (``update_cache``): lädt nur, was fehlt —
   ein Symbol wird übersprungen, wenn der Cache den Stichtag abdeckt oder
   heute schon ein Abruf stattfand (Tagesdaten ändern sich intraday nicht).
   Rückwirkende Adjustierungen (Splits/Dividenden) werden über einen
   Overlap-Vergleich erkannt und lösen einen Full-Refetch aus.
3. **Preis-Panel in EUR** (``load_price_panel``): rechnet strikt aus dem
   Cache (keine API-Calls), Kalender = Handelstage des Benchmarks,
   Forward-Fill max. ``FFILL_LIMIT`` Tage, darüber hinaus Datenlücke.
   Lokalwährungen werden per FX_DAILY nach EUR umgerechnet (GBX/GBp ÷ 100
   über GBP) — das bildet die EUR-Sicht ohne Currency-Hedging ab.

``av_client`` ist Modul-Attribut, damit Tests den HTTP-Layer als Ganzes
per monkeypatch ersetzen können.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

import pandas as pd

from . import av_client, av_store
from .config import Settings
from .ticker_mapping import _SHARE_CLASS_MAP, classify_region

log = logging.getLogger(__name__)

# Forward-Fill-Grenze in Handelstagen; alles darüber zählt als Datenlücke.
FFILL_LIMIT = 3
# Relative Abweichung am Overlap-Tag, ab der eine rückwirkende Adjustierung
# angenommen und die volle Historie neu geladen wird.
OVERLAP_TOLERANCE = 0.005
# Bis zu dieser Lücke (Kalendertage) reicht outputsize=compact (100 Punkte).
COMPACT_GAP_DAYS = 100
# Mindest-Match-Score, damit ein SYMBOL_SEARCH-Treffer ohne exakten
# Symbol-Match akzeptiert wird.
MIN_MATCH_SCORE = 0.4

# Koyfin-/Yahoo-Suffix → Alpha-Vantage-Suffix. Nicht gelistete Suffixe
# laufen über die SYMBOL_SEARCH-Validierung; ein falscher Kandidat wird
# dort verworfen, nie blind übernommen.
_SUFFIX_TO_AV = {
    "DE": "DEX",
    "DEX": "DEX",
    "F": "FRK",
    "FRK": "FRK",
    "L": "LON",
    "LON": "LON",
    "TO": "TRT",
    "TRT": "TRT",
    "V": "TRV",
    "PA": "PAR",
    "PAR": "PAR",
    "AS": "AMS",
    "BR": "BRU",
    "MC": "MAD",
    "MI": "MIL",
    "SW": "SWX",
    "ST": "STO",
    "CO": "CPH",
    "HE": "HEL",
    "OL": "OSL",
    "SA": "SAO",
}

_FX_PREFIX = "FX:"
_MACRO_Y10 = "MACRO:Y10"
_MACRO_WTI = "MACRO:WTI"


@dataclass
class ResolvedSymbol:
    ticker: str
    av_symbol: str
    currency: str
    source: str  # "mapping" | "heuristik" | "suche"
    confirmed: bool = False


@dataclass
class DataQuality:
    """Sammelbecken für den Datenqualitätsteil des Reports."""

    unresolved: list[str] = field(default_factory=list)
    missing_cache: list[str] = field(default_factory=list)
    gaps: dict[str, int] = field(default_factory=dict)
    last_price: dict[str, date] = field(default_factory=dict)
    fetched_at: datetime | None = None
    notes: list[str] = field(default_factory=list)


@dataclass
class PricePanel:
    """EUR-Preis-Panel auf dem Benchmark-Kalender."""

    prices_eur: pd.DataFrame  # Spalten = App-Ticker
    benchmark: pd.Series  # Benchmark in EUR
    quality: DataQuality


def _fx_currency(currency: str) -> tuple[str, float]:
    """Normalisiert Pence-Notierungen: (FX-Währung, Preisfaktor)."""

    if currency.upper() in {"GBX", "GBP."} or currency == "GBp":
        return "GBP", 0.01
    return currency.upper(), 1.0


def _universe_hint(universe: pd.DataFrame | None, ticker: str) -> dict:
    """Name/Region der Universums-Zeile zu einem Schlüssel (uid oder Ticker).

    uid-Treffer haben Vorrang — bei Ticker-Kollisionen (z. B. zwei "SAN")
    liefert der Ticker-Fallback sonst die Hints der falschen Firma."""
    from .uid import row_by_uid

    if universe is None or universe.empty or "ticker" not in universe.columns:
        return {}
    row = row_by_uid(universe, ticker)
    if row is None:
        return {}
    return {
        "name": str(row.get("name") or ""),
        "region": row.get("region"),
    }


def _heuristic_candidates(ticker: str) -> list[str]:
    from .uid import base_ticker

    # Suffix-/Share-Class-Heuristik arbeitet auf dem reinen Börsensymbol;
    # ein uid-Namenszusatz (``SAN~sanofi``) ist kein AV-Symbolbestandteil.
    ticker = base_ticker(ticker) or ticker
    out: list[str] = []
    if "." in ticker:
        base, _, suffix = ticker.rpartition(".")
        av_suffix = _SUFFIX_TO_AV.get(suffix.upper())
        if av_suffix:
            out.append(f"{base}.{av_suffix}")
        out.append(ticker)
    else:
        mapped = _SHARE_CLASS_MAP.get(ticker)
        if mapped:
            out.append(mapped)
        out.append(ticker)
    return out


def _name_match_bonus(entry_name: str, target_name: str) -> float:
    """Namensähnlichkeits-Bonus (0 / 0,5 / 1,0) auf den AV-``match_score``.

    Slug-Vergleich (nur [a-z0-9]) analog ``uid.slugify_name``: exakter Slug
    zählt voll, Enthaltensein in eine Richtung halb. Erst dieser Bonus macht
    die Auflösung bei geteilten Symbolen (Sanofi vs. Banco Santander, beide
    "SAN") deterministisch — der reine ``match_score`` unterscheidet die
    beiden Nicht-US-Treffer nicht."""
    from .uid import slugify_name

    a = slugify_name(entry_name)
    b = slugify_name(target_name)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.5
    return 0.0


def _pick_search_match(
    results: list[dict], region_hint: str, name_hint: str = ""
) -> dict | None:
    """Bester plausibler Treffer: nur Equity/ETF, Mindest-Score, bei
    Non-US-Hinweis werden US-Listings (OTC-Doppellistings!) nachrangig;
    Namensähnlichkeit zum Universums-Namen fließt als Bonus ein."""

    usable = [
        r
        for r in results
        if r.get("type") in {"Equity", "ETF"}
        and r.get("match_score", 0.0) >= MIN_MATCH_SCORE
        and r.get("symbol")
    ]
    if not usable:
        return None
    if region_hint == "non_us":
        non_us = [r for r in usable if r.get("region") != "United States"]
        if non_us:
            usable = non_us
    return max(
        usable,
        key=lambda r: (
            r.get("match_score", 0.0)
            + _name_match_bonus(str(r.get("name") or ""), name_hint)
        ),
    )


def resolve_symbols(
    tickers: list[str],
    universe: pd.DataFrame | None,
    settings: Settings,
    persist: bool = True,
) -> tuple[dict[str, ResolvedSymbol], list[str]]:
    """Löst App-Ticker in AV-Symbole auf (inkl. Währung).

    Liefert ``(aufgelöst, nicht_auflösbar)``. Neue Auflösungen werden —
    unbestätigt — persistiert, damit Folgeläufe keine Such-Calls mehr
    brauchen. API-Fehler bei der Suche machen den Ticker unauflösbar,
    brechen aber nicht den ganzen Lauf ab.
    """

    from .uid import UID_SEPARATOR

    rpm = settings.risk_av_requests_per_minute
    resolved: dict[str, ResolvedSymbol] = {}
    unresolved: list[str] = []

    for ticker in tickers:
        stored = av_store.load_av_mapping(ticker)
        if stored:
            resolved[ticker] = ResolvedSymbol(
                ticker=ticker,
                av_symbol=stored["av_symbol"],
                currency=stored["currency"] or "USD",
                source="mapping",
                confirmed=stored["confirmed"],
            )
            continue

        hint = _universe_hint(universe, ticker)
        region_hint = classify_region(hint.get("region"))
        name_hint = str(hint.get("name") or "")
        # Bei Ticker-Kollisionen (uid mit Namenszusatz) ist ein exakter
        # Symbol-Treffer NICHT beweisend — beide Firmen teilen das Symbol.
        # Dann entscheidet ausschließlich das namensgerankte Matching.
        is_collision = UID_SEPARATOR in str(ticker)
        found: ResolvedSymbol | None = None
        try:
            for candidate in _heuristic_candidates(ticker):
                results = av_client.fetch_symbol_search(candidate, rpm)
                exact = next(
                    (r for r in results if r["symbol"] == candidate), None
                )
                if exact and not is_collision:
                    found = ResolvedSymbol(
                        ticker=ticker,
                        av_symbol=exact["symbol"],
                        currency=exact["currency"] or "USD",
                        source="heuristik",
                    )
                    break
                # Kein Zusatz-Call: die Kandidaten-Suche liefert bereits
                # die Trefferliste für die freie Auswahl.
                match = _pick_search_match(results, region_hint, name_hint)
                if match:
                    found = ResolvedSymbol(
                        ticker=ticker,
                        av_symbol=match["symbol"],
                        currency=match["currency"] or "USD",
                        source="suche",
                    )
                    break
        except av_client.AlphaVantageError as exc:
            log.warning("Symbol-Suche für %s fehlgeschlagen: %s", ticker, exc)

        if found is None:
            unresolved.append(ticker)
            continue
        resolved[ticker] = found
        if persist:
            try:
                av_store.save_av_mapping(
                    ticker, found.av_symbol, found.currency, confirmed=False
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("AV-Mapping für %s nicht gespeichert: %s", ticker, exc)

    return resolved, unresolved


def _needs_fetch(meta: dict | None, asof: date, today: date) -> bool:
    if meta is None:
        return True
    last_refreshed = meta.get("last_refreshed")
    if last_refreshed is not None and last_refreshed >= asof:
        return False
    fetched_at = meta.get("fetched_at")
    if fetched_at is not None and fetched_at.date() >= today:
        # Heute schon abgerufen: Tagesdaten ändern sich intraday nicht mehr.
        return False
    return True


def _outputsize(meta: dict | None, today: date) -> str:
    if meta is None or meta.get("last_refreshed") is None:
        return "full"
    gap = (today - meta["last_refreshed"]).days
    return "compact" if gap <= COMPACT_GAP_DAYS else "full"


def _update_equity(
    symbol: str,
    currency: str,
    kind: str,
    asof: date,
    today: date,
    now: datetime,
    rpm: int,
) -> int:
    """Inkrementelles Update einer Aktien-/ETF-Reihe. Liefert API-Call-Zahl."""

    meta = av_store.get_symbol_meta(symbol)
    if not _needs_fetch(meta, asof, today):
        return 0

    calls = 0
    size = _outputsize(meta, today)
    fetched = av_client.fetch_daily_adjusted(symbol, size, rpm)
    calls += 1

    # Rückwirkende Adjustierung erkennen: weicht der Adjusted Close am
    # letzten bereits gecachten Tag ab, ist die ganze Historie neu skaliert.
    if size == "compact":
        cached = av_store.load_prices(symbol)
        overlap = cached.index.intersection(fetched.index)
        if len(overlap):
            day = overlap[-1]
            old = float(cached.loc[day, "adj_close"])
            new = float(fetched.loc[day, "adj_close"])
            if old and abs(new - old) / abs(old) > OVERLAP_TOLERANCE:
                log.info(
                    "Adjustierung bei %s erkannt (%s: %.4f → %.4f), "
                    "lade volle Historie neu.",
                    symbol,
                    day.date(),
                    old,
                    new,
                )
                fetched = av_client.fetch_daily_adjusted(symbol, "full", rpm)
                calls += 1

    av_store.save_prices(symbol, fetched)
    av_store.set_symbol_meta(
        symbol,
        currency,
        kind,
        fetched.index.max().date() if len(fetched) else None,
        now,
    )
    return calls


def _update_series(
    symbol: str, fetch, asof: date, today: date, now: datetime
) -> int:
    """Update einer FX-/Makro-Reihe (``fetch`` liefert eine Series)."""

    meta = av_store.get_symbol_meta(symbol)
    if not _needs_fetch(meta, asof, today):
        return 0
    series = fetch()
    df = pd.DataFrame({"adj_close": series})
    av_store.save_prices(symbol, df)
    av_store.set_symbol_meta(
        symbol,
        None,
        "reihe",
        series.dropna().index.max().date() if series.notna().any() else None,
        now,
    )
    return 1


def update_cache(
    tickers: list[str],
    universe: pd.DataFrame | None,
    settings: Settings,
    asof: date | None = None,
    today: date | None = None,
) -> dict:
    """Lädt/aktualisiert den Kurscache für Portfolio, Benchmark, FX und
    Makro-Reihen. Rechnet nichts — reines Laden/Cachen für den CLI-Befehl
    ``update`` und den täglichen Scheduler-Lauf.

    Liefert eine Zusammenfassung (aktualisiert/übersprungen/unauflösbar,
    API-Call-Zahl). Einzelne fehlschlagende Symbole werden übersprungen und
    in ``fehler`` gemeldet, damit ein Titel nicht den Gesamtlauf stoppt.
    """

    today = today or date.today()
    asof = asof or today
    # Zeitstempel konsequent aus ``today`` ableiten, damit der
    # „heute schon abgerufen"-Skip auch mit injiziertem Datum (Tests,
    # Nachläufe) konsistent bleibt.
    now = datetime.combine(today, datetime.now().time())
    rpm = settings.risk_av_requests_per_minute

    resolved, unresolved = resolve_symbols(tickers, universe, settings)

    bm_symbol = settings.risk_benchmark_symbol
    targets: list[tuple[str, str, str]] = [
        (bm_symbol, "USD", "benchmark")
    ] + [(r.av_symbol, r.currency, "aktie") for r in resolved.values()]

    summary = {
        "aktualisiert": [],
        "uebersprungen": [],
        "fehler": [],
        "nicht_aufloesbar": unresolved,
        "api_calls": 0,
    }

    currencies: set[str] = {"USD"}
    for symbol, currency, kind in targets:
        fx_ccy, _ = _fx_currency(currency)
        if fx_ccy != "EUR":
            currencies.add(fx_ccy)
        try:
            calls = _update_equity(symbol, currency, kind, asof, today, now, rpm)
        except av_client.AlphaVantageError as exc:
            log.warning("Update für %s fehlgeschlagen: %s", symbol, exc)
            summary["fehler"].append(f"{symbol}: {exc}")
            continue
        summary["api_calls"] += calls
        (summary["aktualisiert"] if calls else summary["uebersprungen"]).append(
            symbol
        )

    for ccy in sorted(currencies):
        symbol = f"{_FX_PREFIX}{ccy}EUR"
        try:
            calls = _update_series(
                symbol,
                lambda c=ccy: av_client.fetch_fx_daily(c, "EUR", "full", rpm),
                asof,
                today,
                now,
            )
        except av_client.AlphaVantageError as exc:
            log.warning("FX-Update %s fehlgeschlagen: %s", symbol, exc)
            summary["fehler"].append(f"{symbol}: {exc}")
            continue
        summary["api_calls"] += calls
        (summary["aktualisiert"] if calls else summary["uebersprungen"]).append(
            symbol
        )

    for symbol, fetch in (
        (_MACRO_Y10, lambda: av_client.fetch_treasury_yield_10y(rpm)),
        (_MACRO_WTI, lambda: av_client.fetch_wti(rpm)),
    ):
        try:
            calls = _update_series(symbol, fetch, asof, today, now)
        except av_client.AlphaVantageError as exc:
            log.warning("Makro-Update %s fehlgeschlagen: %s", symbol, exc)
            summary["fehler"].append(f"{symbol}: {exc}")
            continue
        summary["api_calls"] += calls
        (summary["aktualisiert"] if calls else summary["uebersprungen"]).append(
            symbol
        )

    return summary


def _to_eur(
    prices: pd.Series,
    currency: str,
    calendar: pd.DatetimeIndex,
    fx_cache: dict[str, pd.Series],
) -> pd.Series | None:
    """Reindext auf den Kalender (ffill max. FFILL_LIMIT) und rechnet nach
    EUR um. ``None``, wenn die nötige FX-Reihe fehlt."""

    aligned = prices.reindex(calendar).ffill(limit=FFILL_LIMIT)
    fx_ccy, factor = _fx_currency(currency)
    if fx_ccy == "EUR":
        return aligned * factor
    fx = fx_cache.get(fx_ccy)
    if fx is None:
        fx_df = av_store.load_prices(f"{_FX_PREFIX}{fx_ccy}EUR")
        if fx_df.empty:
            return None
        fx = fx_df["adj_close"]
        fx_cache[fx_ccy] = fx
    fx_aligned = fx.reindex(calendar).ffill(limit=FFILL_LIMIT)
    return aligned * factor * fx_aligned


def load_price_panel(
    tickers: list[str], settings: Settings, asof: date
) -> PricePanel:
    """Baut das EUR-Preis-Panel strikt aus dem Cache (keine API-Calls).

    Raised ``ValueError`` mit deutscher Meldung, wenn der Benchmark-Cache
    fehlt (dann wurde ``update`` noch nie ausgeführt); fehlende Einzeltitel
    landen stattdessen in ``quality``.
    """

    quality = DataQuality()
    bm_symbol = settings.risk_benchmark_symbol
    bm_local = av_store.load_prices(bm_symbol, until=asof)
    if bm_local.empty:
        raise ValueError(
            f"Kein Kurscache für Benchmark {bm_symbol!r} — bitte zuerst "
            "'python -m app.tools.risk_report update' ausführen."
        )

    calendar = bm_local.index
    fx_cache: dict[str, pd.Series] = {}

    bm_meta = av_store.get_symbol_meta(bm_symbol) or {}
    bm_eur = _to_eur(
        bm_local["adj_close"], bm_meta.get("currency") or "USD", calendar, fx_cache
    )
    if bm_eur is None:
        raise ValueError(
            "FX-Reihe für die Benchmark-Umrechnung fehlt im Cache — bitte "
            "'python -m app.tools.risk_report update' ausführen."
        )
    bm_eur.name = bm_symbol

    columns: dict[str, pd.Series] = {}
    fetched_ats: list[datetime] = []
    if bm_meta.get("fetched_at"):
        fetched_ats.append(bm_meta["fetched_at"])

    for ticker in tickers:
        mapping = av_store.load_av_mapping(ticker)
        if mapping is None:
            quality.unresolved.append(ticker)
            continue
        symbol = mapping["av_symbol"]
        local = av_store.load_prices(symbol, until=asof)
        if local.empty:
            quality.missing_cache.append(ticker)
            continue
        currency = mapping["currency"] or "USD"
        eur = _to_eur(local["adj_close"], currency, calendar, fx_cache)
        if eur is None:
            quality.missing_cache.append(ticker)
            quality.notes.append(
                f"{ticker}: FX-Reihe {_fx_currency(currency)[0]}EUR fehlt im Cache."
            )
            continue
        columns[ticker] = eur
        first_valid = eur.first_valid_index()
        last_valid = eur.last_valid_index()
        if last_valid is not None:
            quality.last_price[ticker] = last_valid.date()
        if first_valid is not None:
            n_gaps = int(eur.loc[first_valid:].isna().sum())
            if n_gaps:
                quality.gaps[ticker] = n_gaps
        meta = av_store.get_symbol_meta(symbol)
        if meta and meta.get("fetched_at"):
            fetched_ats.append(meta["fetched_at"])

    quality.fetched_at = max(fetched_ats) if fetched_ats else None
    prices = pd.DataFrame(columns, index=calendar)
    return PricePanel(prices_eur=prices, benchmark=bm_eur, quality=quality)


def load_macro(asof: date) -> pd.DataFrame:
    """Makro-Reihen aus dem Cache: ``y10`` (Prozentpunkte), ``wti`` (USD),
    ``eurusd`` (USD je EUR, aus der gecachten USD→EUR-Reihe invertiert).
    Leere Spalten, wenn eine Reihe fehlt — der Aufrufer entscheidet."""

    y10 = av_store.load_prices(_MACRO_Y10, until=asof)["adj_close"]
    wti = av_store.load_prices(_MACRO_WTI, until=asof)["adj_close"]
    usdeur = av_store.load_prices(f"{_FX_PREFIX}USDEUR", until=asof)["adj_close"]
    eurusd = 1.0 / usdeur if len(usdeur) else usdeur
    return pd.DataFrame({"y10": y10, "wti": wti, "eurusd": eurusd})
