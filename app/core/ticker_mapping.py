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

# Substring-Hinweise für die Regions-Klassifikation. Koyfin-Exporte sind in
# den Bezeichnungen nicht einheitlich ("US", "United States", "United States
# of America", "United States and Canada", "Americas", …), daher Substring-
# statt Exakt-Match. US wird VOR Nicht-US geprüft (siehe classify_region) —
# ein reines "Canada" (ohne US-Hinweis) bleibt non_us, da TSX-Titel den
# Yahoo-Suffix .TO brauchen.
_NON_US_HINTS = (
    "europe", "europa",
    "germany", "deutschland",
    "france", "frankreich",
    "italy", "italien",
    "spain", "spanien",
    "netherlands", "niederlande",
    "switzerland", "schweiz",
    "austria", "österreich",
    "belgium", "belgien",
    "sweden", "schweden",
    "norway", "norwegen",
    "denmark", "dänemark",
    "finland", "finnland",
    "portugal",
    "ireland", "irland",
    "united kingdom", "großbritannien", "britain",
    "poland", "polen",
    "asia", "asien",
    "japan",
    "china",
    "hong kong", "hongkong",
    "korea",
    "taiwan",
    "india", "indien",
    "singapore", "singapur",
    "australia", "australien",
    "canada", "kanada",
    "brazil", "brasilien",
    "mexico", "mexiko",
    "emerging",
)

_US_HINTS = (
    "united states",
    "usa",
    "vereinigte staaten",
    "north america",
    "nordamerika",
    "americas",
    "amerika",
)

_US_EXACT = {"us", "u.s.", "u.s.a."}

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


def classify_region(region) -> str:
    """Klassifiziert den Koyfin-Regionswert: ``"us"``, ``"non_us"``, ``"unknown"``.

    US-Hinweise werden VOR den Nicht-US-Hinweisen geprüft: Koyfin fasst
    Regionen zusammen ("United States and Canada"), und dort dominiert die
    US-Interpretation — die Ticker sind in dieser Sammelregion überwiegend
    US-Listings ohne Suffix. Ein reines "Canada" bleibt non_us (.TO-Suffix).
    """
    if not isinstance(region, str) or not region.strip():
        return "unknown"
    value = region.strip().lower()
    if value in _US_EXACT or any(hint in value for hint in _US_HINTS):
        return "us"
    if any(hint in value for hint in _NON_US_HINTS):
        return "non_us"
    return "unknown"


def resolve(ticker: str, region=None) -> str | None:
    """Löst einen Koyfin-Ticker in den Yahoo-Dialekt auf, oder ``None``.

    ``None`` heißt: der Titel braucht mutmaßlich eine Börsen-Endung — der
    Aufrufer muss den Nutzer über die Symbol-Suche bestätigen lassen. US- und
    unbekannte Regionen werden optimistisch durchgereicht, weil Koyfin-Ticker
    bei US-Titeln in der Regel identisch mit den yfinance-Tickern sind; eine
    Fehlzuordnung lässt sich über das Mapping-Modal jederzeit korrigieren.
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

    if classify_region(region) == "non_us":
        return None

    return _SHARE_CLASS_MAP.get(t, t)


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
