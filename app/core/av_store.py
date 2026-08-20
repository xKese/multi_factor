"""DB-Persistenz für den Alpha-Vantage-Kurscache und die Symbol-Mappings.

Drei Tabellen in der bestehenden App-Datenbank (gleicher Dialekt-Rahmen wie
``persistence``: SQLite ∩ Postgres):

- ``av_price_cache``: eine Zeile je (Symbol, Handelstag) mit Adjusted Close
  und Close. FX-Reihen liegen unter synthetischen Symbolen (``FX:USDEUR``),
  Makro-Reihen unter ``MACRO:Y10``/``MACRO:WTI`` (Wert in ``adj_close``).
- ``av_symbol_meta``: Steuerdaten für das inkrementelle Update je Symbol
  (Währung, letzter Kurstag, Zeitpunkt des letzten API-Abrufs).
- ``av_ticker_mappings``: App-Ticker → AV-Symbol inkl. Währung. Bewusst
  getrennt von ``ticker_mappings`` (Yahoo-Dialekt ``SAP.DE`` ≠ AV-Dialekt
  ``SAP.DEX``).

Schreibfunktionen raisen bei DB-Problemen (der CLI-Aufrufer meldet und
bricht ab), Lesefunktionen sind fail-open wie im restlichen Bestand.
"""

from __future__ import annotations

import logging
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import persistence

log = logging.getLogger(__name__)

_PRICE_TABLE = "av_price_cache"
_SYMBOL_META_TABLE = "av_symbol_meta"
_AV_MAPPING_TABLE = "av_ticker_mappings"


def _ensure_price_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_PRICE_TABLE} ("
            "symbol TEXT NOT NULL, "
            "price_date DATE NOT NULL, "
            "adj_close DOUBLE PRECISION, "
            "close DOUBLE PRECISION, "
            "PRIMARY KEY (symbol, price_date))"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_avpc_symbol ON "
            f"{_PRICE_TABLE}(symbol)"
        )
    )


def _ensure_symbol_meta_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SYMBOL_META_TABLE} ("
            "symbol TEXT PRIMARY KEY, "
            "currency TEXT, "
            "kind TEXT, "
            "last_refreshed DATE, "
            "fetched_at TIMESTAMP)"
        )
    )


def _ensure_av_mapping_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_AV_MAPPING_TABLE} ("
            "source_ticker TEXT PRIMARY KEY, "
            "av_symbol TEXT NOT NULL, "
            "currency TEXT, "
            "confirmed_by_user INTEGER NOT NULL DEFAULT 0, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_prices(symbol: str, df: pd.DataFrame) -> int:
    """UPSERT einer Kurs-Historie (Index = Datum, Spalten ``adj_close``,
    optional ``close``). Liefert die Zeilenzahl. Raised bei DB-Problemen."""

    engine = persistence.get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if df is None or df.empty:
        return 0

    rows: list[dict] = []
    close = df["close"] if "close" in df.columns else None
    for i, (day, adj) in enumerate(df["adj_close"].items()):
        if pd.isna(adj):
            continue
        c = close.iloc[i] if close is not None else None
        rows.append(
            {
                "symbol": str(symbol),
                "price_date": pd.Timestamp(day).date(),
                "adj_close": float(adj),
                "close": None if c is None or pd.isna(c) else float(c),
            }
        )
    if not rows:
        return 0

    with engine.begin() as conn:
        _ensure_price_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_PRICE_TABLE} "
                "(symbol, price_date, adj_close, close) "
                "VALUES (:symbol, :price_date, :adj_close, :close) "
                "ON CONFLICT (symbol, price_date) DO UPDATE SET "
                "adj_close = EXCLUDED.adj_close, "
                "close = EXCLUDED.close"
            ),
            rows,
        )
    return len(rows)


def load_prices(symbol: str, until: date | None = None) -> pd.DataFrame:
    """Kurs-Historie eines Symbols aus dem Cache (DatetimeIndex aufsteigend,
    Spalten ``adj_close``, ``close``). Leer bei DB-Fehlern. Raised nie."""

    empty = pd.DataFrame(columns=["adj_close", "close"])
    engine = persistence.get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_price_table(conn)
            sql = (
                f"SELECT price_date, adj_close, close FROM {_PRICE_TABLE} "
                "WHERE symbol = :s"
            )
            params: dict = {"s": str(symbol)}
            if until is not None:
                sql += " AND price_date <= :until"
                params["until"] = until
            sql += " ORDER BY price_date ASC"
            df = pd.read_sql(text(sql), conn, params=params)
    except SQLAlchemyError as exc:
        log.warning("Laden des Kurscaches für %s fehlgeschlagen: %s", symbol, exc)
        return empty
    if df.empty:
        return empty
    df["price_date"] = pd.to_datetime(df["price_date"])
    return df.set_index("price_date")[["adj_close", "close"]]


def get_symbol_meta(symbol: str) -> dict | None:
    """Steuerdaten eines Symbols (``currency``, ``kind``, ``last_refreshed``
    als ``date``, ``fetched_at`` als ``datetime``) oder ``None``. Raised nie."""

    engine = persistence.get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_symbol_meta_table(conn)
            row = conn.execute(
                text(
                    f"SELECT currency, kind, last_refreshed, fetched_at "
                    f"FROM {_SYMBOL_META_TABLE} WHERE symbol = :s"
                ),
                {"s": str(symbol)},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden der Symbol-Meta für %s fehlgeschlagen: %s", symbol, exc)
        return None
    if row is None:
        return None
    last_refreshed = row[2]
    if last_refreshed is not None:
        last_refreshed = pd.Timestamp(last_refreshed).date()
    fetched_at = row[3]
    if fetched_at is not None and not isinstance(fetched_at, datetime):
        fetched_at = pd.Timestamp(fetched_at).to_pydatetime()
    return {
        "currency": row[0],
        "kind": row[1],
        "last_refreshed": last_refreshed,
        "fetched_at": fetched_at,
    }


def set_symbol_meta(
    symbol: str,
    currency: str | None,
    kind: str,
    last_refreshed: date | None,
    fetched_at: datetime,
) -> None:
    """UPSERT der Steuerdaten eines Symbols. Raised bei DB-Problemen."""

    engine = persistence.get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    with engine.begin() as conn:
        _ensure_symbol_meta_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_SYMBOL_META_TABLE} "
                "(symbol, currency, kind, last_refreshed, fetched_at) "
                "VALUES (:symbol, :currency, :kind, :last_refreshed, :fetched_at) "
                "ON CONFLICT (symbol) DO UPDATE SET "
                "currency = EXCLUDED.currency, "
                "kind = EXCLUDED.kind, "
                "last_refreshed = EXCLUDED.last_refreshed, "
                "fetched_at = EXCLUDED.fetched_at"
            ),
            {
                "symbol": str(symbol),
                "currency": currency,
                "kind": kind,
                "last_refreshed": last_refreshed,
                "fetched_at": fetched_at,
            },
        )


def save_av_mapping(
    source_ticker: str,
    av_symbol: str,
    currency: str | None,
    confirmed: bool = False,
) -> None:
    """UPSERT eines App-Ticker→AV-Symbol-Mappings. Raised bei DB-Problemen."""

    engine = persistence.get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    with engine.begin() as conn:
        _ensure_av_mapping_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_AV_MAPPING_TABLE} "
                "(source_ticker, av_symbol, currency, confirmed_by_user, "
                "updated_at) "
                "VALUES (:s, :a, :c, :conf, CURRENT_TIMESTAMP) "
                "ON CONFLICT (source_ticker) DO UPDATE SET "
                "av_symbol = EXCLUDED.av_symbol, "
                "currency = EXCLUDED.currency, "
                "confirmed_by_user = EXCLUDED.confirmed_by_user, "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {
                "s": str(source_ticker),
                "a": str(av_symbol),
                "c": currency,
                "conf": 1 if confirmed else 0,
            },
        )


def load_av_mapping(source_ticker: str) -> dict | None:
    """Gespeichertes Mapping (``av_symbol``, ``currency``, ``confirmed``)
    für einen App-Ticker oder ``None``. Raised nie."""

    engine = persistence.get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_av_mapping_table(conn)
            row = conn.execute(
                text(
                    f"SELECT av_symbol, currency, confirmed_by_user "
                    f"FROM {_AV_MAPPING_TABLE} WHERE source_ticker = :s"
                ),
                {"s": str(source_ticker)},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden des AV-Mappings fehlgeschlagen: %s", exc)
        return None
    if row is None:
        return None
    return {
        "av_symbol": row[0],
        "currency": row[1],
        "confirmed": bool(row[2]),
    }
