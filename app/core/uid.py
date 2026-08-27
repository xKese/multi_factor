"""Eindeutige interne Kennung (uid) je Universums-Zeile.

Koyfin-Ticker tragen kein Börsensuffix — verschiedene Aktien können dasselbe
Symbol haben (z. B. Sanofi und Banco Santander, beide "SAN"). Die uid macht
jede Zeile adressierbar, ohne bestehende Daten zu invalidieren:

- Eindeutiger Ticker im Universum → ``uid == ticker`` (der Normalfall; alle
  bestehenden URLs, Mappings und Historien-Zeilen bleiben damit gültig).
- Kollidierender Ticker → ``uid = "<ticker>~<namens-slug>"``, z. B.
  ``SAN~sanofi`` und ``SAN~bancosantander``. ``~`` ist in URLs unreserviert
  (RFC 3986) und kommt in Koyfin-Tickern nicht vor.

Die uid ist ein reiner Schlüssel (URLs, Dropdown-Values, DB-Keys) — angezeigt
wird weiterhin der Ticker. Regionsdaten reichen zur Unterscheidung nicht aus
(Sanofi und Santander sind beide "EU"), daher basiert der Suffix auf dem
Firmennamen.
"""

from __future__ import annotations

import re

import pandas as pd

UID_SEPARATOR = "~"

# Slug-Länge begrenzen, damit uids in URLs/Tabellen handlich bleiben; 24
# Zeichen reichen, um reale Namenskollisionen praktisch auszuschließen.
_SLUG_MAX_LEN = 24
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify_name(name: object) -> str:
    """Firmenname → stabiler Kleinbuchstaben-Slug (nur [a-z0-9])."""
    if name is None or (isinstance(name, float) and pd.isna(name)):
        return ""
    slug = _SLUG_RE.sub("", str(name).lower())
    return slug[:_SLUG_MAX_LEN]


def base_ticker(uid: object) -> str:
    """Ticker-Anteil einer uid (Teil vor dem Separator)."""
    if uid is None or (isinstance(uid, float) and pd.isna(uid)):
        return ""
    return str(uid).split(UID_SEPARATOR, 1)[0]


def assign_uids(df: pd.DataFrame) -> pd.DataFrame:
    """Ergänzt/erneuert die Spalte ``uid`` (in-place auf einer Kopie).

    Nur bei Ticker-Kollisionen weicht die uid vom Ticker ab. Kollidieren auch
    die Namens-Slugs (identischer oder fehlender Name), wird ein laufender
    Suffix ``-2``, ``-3``, … angehängt, damit jede Zeile adressierbar bleibt.
    """
    df = df.copy()
    if "ticker" not in df.columns:
        df["uid"] = pd.Series(dtype=str)
        return df

    tickers = df["ticker"].astype(str)
    counts = tickers.value_counts()
    names = df["name"] if "name" in df.columns else pd.Series("", index=df.index)

    uids: list[str] = []
    seen: dict[str, int] = {}
    for ticker, name in zip(tickers, names):
        if counts.get(ticker, 0) <= 1:
            uid = ticker
        else:
            slug = slugify_name(name)
            uid = f"{ticker}{UID_SEPARATOR}{slug}" if slug else ticker
        if uid in seen:
            seen[uid] += 1
            uid = f"{uid}-{seen[uid]}"
        else:
            seen[uid] = 1
        uids.append(uid)
    df["uid"] = uids
    return df


def duplicate_ticker_info(df: pd.DataFrame) -> list[tuple[str, list[tuple[str, str]]]]:
    """Kollidierende Ticker mit ihren (uid, name)-Paaren — für den
    Import-Bericht. Leer, wenn alle Ticker eindeutig sind."""
    if df is None or df.empty or "ticker" not in df.columns:
        return []
    tickers = df["ticker"].astype(str)
    dupes = tickers.value_counts()
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for ticker in sorted(dupes[dupes > 1].index):
        rows = df.loc[tickers == ticker]
        pairs = [
            (str(r.get("uid", ticker)), str(r.get("name") or ""))
            for _, r in rows.iterrows()
        ]
        out.append((ticker, pairs))
    return out


def row_by_uid(df: pd.DataFrame, key: object) -> pd.Series | None:
    """Zeile per uid nachschlagen, mit Ticker-Fallback für Alt-Links.

    Reihenfolge: exakter uid-Treffer → exakter Ticker-Treffer (erste Zeile,
    identisch zum bisherigen Verhalten für Alt-URLs/Bookmarks). ``None``,
    wenn nichts passt."""
    if df is None or df.empty or key is None:
        return None
    key = str(key)
    if "uid" in df.columns:
        hit = df.loc[df["uid"] == key]
        if not hit.empty:
            return hit.iloc[0]
    if "ticker" in df.columns:
        hit = df.loc[df["ticker"].astype(str) == key]
        if not hit.empty:
            return hit.iloc[0]
    return None


def rows_by_uid_index(df: pd.DataFrame, key: object):
    """Index-Positionen wie :func:`row_by_uid` (uid-Treffer vor
    Ticker-Fallback); leerer Index, wenn nichts passt."""
    if df is None or df.empty or key is None:
        return pd.Index([])
    key = str(key)
    if "uid" in df.columns:
        idx = df.index[df["uid"] == key]
        if len(idx):
            return idx
    if "ticker" in df.columns:
        return df.index[df["ticker"].astype(str) == key]
    return pd.Index([])
