"""Koyfin-Ticker → Yahoo-Dialekt-Ticker für den TradingAgents-Service.

Koyfin-Exporte führen Ticker in der Regel ohne Börsen-Endung (z. B. ``MBG``
für Mercedes-Benz statt ``MBG.F``/``MBG.DE``), während yfinance/Alpha Vantage
für europäische Titel den Suffix brauchen. Auflösungsreihenfolge:

1. Vom Nutzer bestätigtes Mapping aus der Datenbank (``ticker_mappings``).
2. US-Heuristik: US-Titel brauchen keinen Suffix; Punktklassen wie ``BRKB``
   werden in Yahoos ``BRK-B``-Schreibweise übersetzt.
3. Sonst ``None`` — die UI muss den Nutzer per Symbol-Suche (TradingAgents
   ``GET /api/symbol-search``, liefert bereits Yahoo-Dialekt) bestätigen
   lassen; das Ergebnis wird als Mapping gespeichert.
"""

from __future__ import annotations

import re

from . import persistence

# Regionen, deren Koyfin-Ticker ohne Suffix direkt Yahoo-kompatibel sind.
_NO_SUFFIX_REGIONS = {
    "united states",
    "usa",
    "us",
    "vereinigte staaten",
}

# Bekannte US-Aktienklassen, die Koyfin ohne Trennzeichen exportiert und
# Yahoo mit Bindestrich führt.
_SHARE_CLASS_MAP = {
    "BRKA": "BRK-A",
    "BRKB": "BRK-B",
    "BFA": "BF-A",
    "BFB": "BF-B",
    "HEIA": "HEI-A",
    "LGFA": "LGF-A",
    "LGFB": "LGF-B",
}

_TICKER_RE = re.compile(r"^[A-Z0-9.\-]{1,15}$")


def is_us_region(region) -> bool:
    return isinstance(region, str) and region.strip().lower() in _NO_SUFFIX_REGIONS


def resolve(ticker: str, region=None) -> str | None:
    """Löst einen Koyfin-Ticker in den Yahoo-Dialekt auf, oder ``None``.

    ``None`` heißt: keine sichere automatische Auflösung möglich — der
    Aufrufer muss den Nutzer über die Symbol-Suche bestätigen lassen.
    """
    if not isinstance(ticker, str) or not ticker.strip():
        return None
    t = ticker.strip().upper()
    if not _TICKER_RE.match(t):
        return None

    stored = persistence.load_ticker_mapping(t)
    if stored:
        return stored

    # Ein bereits suffigierter Ticker (z. B. ``MBG.DE`` aus einem manuellen
    # Import) ist schon Yahoo-kompatibel.
    if "." in t or "-" in t:
        return t

    if is_us_region(region):
        return _SHARE_CLASS_MAP.get(t, t)

    return None


def rank_suggestions(results: list[dict], name=None, region=None) -> list[dict]:
    """Sortiert Symbol-Such-Treffer nach Passung zu Name/Region.

    ``results`` sind Einträge der TradingAgents-Symbol-Suche (bereits im
    Yahoo-Dialekt); erwartet werden Keys wie ``symbol``, ``name``,
    ``region``. Unbekannte Strukturen werden unverändert durchgereicht.
    """
    if not results:
        return []

    target_name = (name or "").strip().lower()
    target_region = (region or "").strip().lower()

    def score(entry: dict) -> float:
        s = 0.0
        entry_name = str(entry.get("name") or "").lower()
        entry_region = str(entry.get("region") or "").lower()
        if target_name and entry_name:
            if entry_name == target_name:
                s += 3.0
            elif target_name.split()[0] in entry_name:
                s += 1.5
        if target_region and entry_region and target_region in entry_region:
            s += 1.0
        return s

    return sorted(results, key=score, reverse=True)
