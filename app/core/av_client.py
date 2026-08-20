"""Alpha-Vantage-HTTP-Client für das Risiko-&-Benchmark-Modul.

Kapselt die vier benötigten Endpunkte (TIME_SERIES_DAILY_ADJUSTED, FX_DAILY,
TREASURY_YIELD, WTI) plus SYMBOL_SEARCH. Der API-Key kommt ausschließlich aus
der Umgebungsvariable ``ALPHAVANTAGE_API_KEY`` (Premium) und wird nie geloggt.

Besonderheiten der API, die dieser Client abfängt:

- Rate-Limit- und Fehlerantworten kommen mit HTTP 200 und einem JSON-Body,
  der statt der Nutzdaten einen der Keys ``"Note"``/``"Information"``/
  ``"Error Message"`` enthält — Erkennung daher am Body, nicht am Status.
- Makro-Reihen (TREASURY_YIELD, WTI) enthalten für Feiertage/Lücken den
  String ``"."`` als Wert → wird zu NaN.
- Die Schemata von SYMBOL_SEARCH, FX_DAILY, TREASURY_YIELD und WTI wurden
  per Beispiel-Call verifiziert; TIME_SERIES_DAILY_ADJUSTED (Premium) wird
  zur Laufzeit strikt validiert und raised mit den tatsächlichen Keys,
  falls das dokumentierte Schema (``"5. adjusted close"``) nicht zutrifft.

Kein Fail-open wie in ``agents_client``: Der Datenlayer (``market_data``)
entscheidet, welche Fehler tolerierbar sind — hier wird geraist.
``requests`` und ``time`` sind Modul-Attribute, damit Tests sie per
monkeypatch ersetzen können.
"""

from __future__ import annotations

import logging
import os
import threading
import time

import pandas as pd
import requests

log = logging.getLogger(__name__)

BASE_URL = "https://www.alphavantage.co/query"

# (connect, read) — Full-Historien können mehrere MB groß sein.
_TIMEOUT = (10, 120)

# Backoff-Wartezeiten je Retry. Drei Versuche insgesamt: das per-Minute-
# Rate-Limit des Premium-Plans erholt sich in dieser Spanne sicher.
RETRY_DELAYS: tuple[float, ...] = (2.0, 8.0, 30.0)

# Body-Keys, die eine Fehler-/Limit-Antwort markieren (HTTP 200!).
_RATE_LIMIT_KEYS = ("Note", "Information")
_ERROR_KEY = "Error Message"


class AlphaVantageError(RuntimeError):
    """Fehler des AV-Clients. ``kind``: ``"rate_limit"`` (Limit trotz
    Retries nicht erholt), ``"error"`` (API-Fehler, z. B. unbekanntes
    Symbol, fehlender Key), ``"parse"`` (unerwartetes Antwortschema)."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def api_key() -> str | None:
    key = os.getenv("ALPHAVANTAGE_API_KEY", "").strip()
    return key or None


class _RateLimiter:
    """Erzwingt einen Mindestabstand zwischen Requests (prozessweit)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self, requests_per_minute: int) -> None:
        if requests_per_minute <= 0:
            return
        min_gap = 60.0 / float(requests_per_minute)
        with self._lock:
            now = time.monotonic()
            gap = now - self._last
            if gap < min_gap:
                time.sleep(min_gap - gap)
            self._last = time.monotonic()


_limiter = _RateLimiter()


def _get(params: dict, requests_per_minute: int = 70) -> dict:
    """GET gegen die AV-API mit Rate-Limit, Retry und Body-Fehlererkennung.

    Raised ``AlphaVantageError``; ``"Error Message"`` (z. B. ungültiges
    Symbol) wird nicht retried, Limit-/Netzwerkfehler schon.
    """

    key = api_key()
    if not key:
        raise AlphaVantageError(
            "ALPHAVANTAGE_API_KEY ist nicht gesetzt", kind="error"
        )

    query = {**params, "apikey": key, "datatype": "json"}
    last_message = "unbekannter Fehler"
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        _limiter.wait(requests_per_minute)
        try:
            resp = requests.get(BASE_URL, params=query, timeout=_TIMEOUT)
        except requests.RequestException as exc:
            last_message = f"Netzwerkfehler: {exc}"
            log.info("AV-Request fehlgeschlagen (Versuch %d): %s", attempt + 1, exc)
            continue
        if resp.status_code != 200:
            last_message = f"HTTP {resp.status_code}"
            log.info("AV-Request HTTP %s (Versuch %d)", resp.status_code, attempt + 1)
            continue
        try:
            data = resp.json()
        except ValueError as exc:
            last_message = f"Antwort ist kein JSON: {exc}"
            log.info("AV-Antwort unlesbar (Versuch %d): %s", attempt + 1, exc)
            continue
        if not isinstance(data, dict):
            last_message = "Antwort ist kein JSON-Objekt"
            continue
        if _ERROR_KEY in data:
            raise AlphaVantageError(
                f"API-Fehler für {params.get('function')} "
                f"{params.get('symbol', params.get('keywords', ''))}: "
                f"{data[_ERROR_KEY]}",
                kind="error",
            )
        limit_msg = next(
            (data[k] for k in _RATE_LIMIT_KEYS if k in data and len(data) == 1),
            None,
        )
        if limit_msg is not None:
            last_message = f"Rate-Limit: {limit_msg}"
            log.info("AV-Rate-Limit (Versuch %d): %s", attempt + 1, limit_msg)
            continue
        return data

    raise AlphaVantageError(
        f"AV-Request nach {attempts} Versuchen aufgegeben — {last_message}",
        kind="rate_limit" if "Rate-Limit" in last_message else "error",
    )


def _parse_numbered_series(
    payload: dict, series_key: str, value_key: str, context: str
) -> pd.Series:
    """Extrahiert aus einem ``{"YYYY-MM-DD": {"1. open": …}}``-Block die
    Werte unter ``value_key`` als float-Series (Datum aufsteigend)."""

    block = payload.get(series_key)
    if not isinstance(block, dict) or not block:
        raise AlphaVantageError(
            f"{context}: Key {series_key!r} fehlt oder ist leer "
            f"(vorhandene Keys: {sorted(payload.keys())})",
            kind="parse",
        )
    first = next(iter(block.values()))
    if value_key not in first:
        raise AlphaVantageError(
            f"{context}: Feld {value_key!r} fehlt im Tagesblock "
            f"(vorhandene Felder: {sorted(first.keys())})",
            kind="parse",
        )
    values = {day: row.get(value_key) for day, row in block.items()}
    series = pd.Series(values, dtype="object")
    series.index = pd.to_datetime(series.index)
    series = pd.to_numeric(series, errors="coerce").sort_index()
    return series


def fetch_daily_adjusted(
    symbol: str, outputsize: str = "full", requests_per_minute: int = 70
) -> pd.DataFrame:
    """Adjusted-Close-Historie eines Symbols.

    Liefert einen DataFrame mit DatetimeIndex (aufsteigend) und Spalten
    ``adj_close`` und ``close``. ``adj_close`` ist wegen Splits/Dividenden
    die Rechengrundlage aller Renditen.
    """

    data = _get(
        {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": outputsize,
        },
        requests_per_minute,
    )
    context = f"TIME_SERIES_DAILY_ADJUSTED {symbol}"
    adj = _parse_numbered_series(
        data, "Time Series (Daily)", "5. adjusted close", context
    )
    close = _parse_numbered_series(data, "Time Series (Daily)", "4. close", context)
    return pd.DataFrame({"adj_close": adj, "close": close})


def fetch_fx_daily(
    from_symbol: str,
    to_symbol: str = "EUR",
    outputsize: str = "full",
    requests_per_minute: int = 70,
) -> pd.Series:
    """Tages-Schlusskurse eines Währungspaars (z. B. USD→EUR), aufsteigend."""

    data = _get(
        {
            "function": "FX_DAILY",
            "from_symbol": from_symbol,
            "to_symbol": to_symbol,
            "outputsize": outputsize,
        },
        requests_per_minute,
    )
    return _parse_numbered_series(
        data,
        "Time Series FX (Daily)",
        "4. close",
        f"FX_DAILY {from_symbol}{to_symbol}",
    )


def _parse_macro_series(data: dict, context: str) -> pd.Series:
    rows = data.get("data")
    if not isinstance(rows, list) or not rows:
        raise AlphaVantageError(
            f"{context}: Key 'data' fehlt oder ist leer "
            f"(vorhandene Keys: {sorted(data.keys())})",
            kind="parse",
        )
    # "." markiert fehlende Tage (Feiertage) → NaN.
    values = {r.get("date"): r.get("value") for r in rows if r.get("date")}
    series = pd.Series(values, dtype="object")
    series.index = pd.to_datetime(series.index)
    return pd.to_numeric(series, errors="coerce").sort_index()


def fetch_treasury_yield_10y(requests_per_minute: int = 70) -> pd.Series:
    """10-jährige US-Treasury-Rendite, täglich, in Prozentpunkten."""

    data = _get(
        {"function": "TREASURY_YIELD", "interval": "daily", "maturity": "10year"},
        requests_per_minute,
    )
    return _parse_macro_series(data, "TREASURY_YIELD 10y")


def fetch_wti(requests_per_minute: int = 70) -> pd.Series:
    """WTI-Rohölpreis, täglich, in USD je Barrel."""

    data = _get({"function": "WTI", "interval": "daily"}, requests_per_minute)
    return _parse_macro_series(data, "WTI")


def fetch_symbol_search(
    query: str, requests_per_minute: int = 70
) -> list[dict]:
    """SYMBOL_SEARCH-Treffer mit normalisierten Keys.

    Liefert Dicts mit ``symbol``, ``name``, ``type``, ``region``,
    ``currency``, ``match_score`` (float), sortiert wie von der API
    geliefert (bester Treffer zuerst).
    """

    data = _get(
        {"function": "SYMBOL_SEARCH", "keywords": query}, requests_per_minute
    )
    matches = data.get("bestMatches")
    if not isinstance(matches, list):
        raise AlphaVantageError(
            f"SYMBOL_SEARCH {query!r}: Key 'bestMatches' fehlt "
            f"(vorhandene Keys: {sorted(data.keys())})",
            kind="parse",
        )
    out: list[dict] = []
    for m in matches:
        try:
            score = float(m.get("9. matchScore", 0.0))
        except (TypeError, ValueError):
            score = 0.0
        out.append(
            {
                "symbol": str(m.get("1. symbol") or ""),
                "name": str(m.get("2. name") or ""),
                "type": str(m.get("3. type") or ""),
                "region": str(m.get("4. region") or ""),
                "currency": str(m.get("8. currency") or ""),
                "match_score": score,
            }
        )
    return out
