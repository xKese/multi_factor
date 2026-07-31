"""Datenbank-Persistenz für das importierte Koyfin-Universum.

Speichert die Rohdaten genau so, wie ``load_koyfin_csv`` sie liefert, und lädt
sie beim App-Start zurück in ``STATE``. Fehler sind „fail-open": Engine-Bau
und Load dürfen nicht raisen, damit die App auch ohne DB startet.

Default ist eine lokale SQLite-Datei (``data/multifactor.db``); über die
Umgebungsvariable ``DATABASE_URL`` kann alternativ z. B. PostgreSQL genutzt
werden. Das SQL ist auf den gemeinsamen Dialekt beider Datenbanken beschränkt
(``CURRENT_TIMESTAMP`` statt ``now()``, ``TEXT`` statt ``JSONB``,
``ON CONFLICT … DO UPDATE`` gibt es in beiden).
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import fields as dataclass_fields
from datetime import date

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings

log = logging.getLogger(__name__)

_DEFAULT_URL = "sqlite:///data/multifactor.db"
_UNIVERSE_TABLE = "koyfin_universe"
_META_TABLE = "koyfin_meta"
_SECTOR_SNAPSHOT_TABLE = "sector_momentum_snapshots"
_SECTOR_SCORE_HISTORY_TABLE = "sector_score_history"
_SIGNAL_HISTORY_TABLE = "universe_signal_history"
_MS_PORTFOLIO_TABLE = "ms_portfolio"
_SETTINGS_TABLE = "app_settings"
_FACTOR_TIMING_TABLE = "factor_timing_inputs"
_AGENT_ANALYSES_TABLE = "agent_analyses"
_TICKER_MAPPINGS_TABLE = "ticker_mappings"

# Vollständige Liste der Eingabefelder der Factor-Timing-Seite (Makro-,
# Sentiment- und Faktor-Momentum-Werte). Wird beim Persistieren als JSON-Text
# abgelegt; unbekannte Keys werden beim Laden ignoriert, fehlende kehren als
# ``None`` zurück (Aufrufer mergt mit Defaults).
_FACTOR_TIMING_FIELDS: tuple[str, ...] = (
    "pmi",
    "pmi_trend",
    "cli",
    "spread",
    "cpi",
    "vix",
    "credit",
    "pcr",
    "flows",
    "mom_value",
    "mom_quality",
    "mom_growth",
    "mom_momentum",
    "mom_lowvol",
)

_SETTINGS_FIELDS: tuple[str, ...] = (
    "factor_weights",
    "value_weights",
    "quality_weights",
    "growth_weights",
    "momentum_weights",
    "lowvol_weights",
    "min_piotroski",
    "min_altman_z",
    "min_market_cap",
    "min_stocks_per_industry",
    "percentile_mode",
    "agents_provider",
    "agents_quick_model",
    "agents_deep_model",
    "agents_depth",
    "agents_language",
    "agents_temperature",
    "agents_prev_analysis",
)

_engine: Engine | None = None
_engine_tried = False


def get_engine() -> Engine | None:
    """Liefert eine zwischengespeicherte SQLAlchemy-Engine oder ``None``.

    Gibt ``None`` zurück, wenn ``create_engine`` fehlschlägt (z. B. ungültige
    URL). Ein Verbindungsfehler zur Laufzeit wird hier *nicht* erkannt — das
    passiert erst bei der tatsächlichen DB-Operation.
    """

    global _engine, _engine_tried
    if _engine is not None or _engine_tried:
        return _engine
    _engine_tried = True
    url = os.getenv("DATABASE_URL", _DEFAULT_URL)
    try:
        kwargs: dict = {"pool_pre_ping": True, "future": True}
        parsed = make_url(url)
        if parsed.get_backend_name() == "sqlite":
            # Dash bedient Callbacks aus mehreren Threads; Verbindungen aus
            # dem Pool dürfen daher nicht an ihren Erzeuger-Thread gebunden
            # sein.
            kwargs["connect_args"] = {"check_same_thread": False}
            if parsed.database and parsed.database != ":memory:":
                db_dir = os.path.dirname(os.path.abspath(parsed.database))
                os.makedirs(db_dir, exist_ok=True)
        _engine = create_engine(url, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("Konnte SQLAlchemy-Engine nicht erstellen: %s", exc)
        _engine = None
    return _engine


def save_universe(df: pd.DataFrame) -> None:
    """Ersetzt den Inhalt von ``koyfin_universe`` durch ``df`` und aktualisiert
    die Meta-Zeile. Raised bei Fehler — der Aufrufer zeigt eine UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    with engine.begin() as conn:
        df.to_sql(_UNIVERSE_TABLE, conn, if_exists="replace", index=False)
        conn.execute(
            text(
                f"CREATE TABLE IF NOT EXISTS {_META_TABLE} ("
                "imported_at TIMESTAMP NOT NULL, "
                "row_count INTEGER NOT NULL)"
            )
        )
        conn.execute(text(f"DELETE FROM {_META_TABLE}"))
        conn.execute(
            text(
                f"INSERT INTO {_META_TABLE} (imported_at, row_count) "
                "VALUES (CURRENT_TIMESTAMP, :n)"
            ),
            {"n": len(df)},
        )


def load_universe() -> pd.DataFrame | None:
    """Lädt das Universum aus der DB. Gibt ``None`` zurück, wenn die Tabelle
    fehlt oder die DB nicht erreichbar ist. Raised nie.
    """

    engine = get_engine()
    if engine is None:
        return None
    try:
        return pd.read_sql_table(_UNIVERSE_TABLE, engine)
    except ValueError:
        log.info("Tabelle %s existiert noch nicht.", _UNIVERSE_TABLE)
        return None
    except SQLAlchemyError as exc:
        log.warning("Laden aus Datenbank fehlgeschlagen: %s", exc)
        return None


def _ensure_sector_snapshot_table(conn) -> None:
    conn.execute(
        text(
            f'CREATE TABLE IF NOT EXISTS {_SECTOR_SNAPSHOT_TABLE} ('
            'snapshot_date DATE NOT NULL, '
            'ticker TEXT NOT NULL, '
            '"group" TEXT NOT NULL, '
            'display_name TEXT NOT NULL, '
            'last_price DOUBLE PRECISION, '
            'sma_50 DOUBLE PRECISION, '
            'sma_200 DOUBLE PRECISION, '
            'momentum TEXT NOT NULL, '
            'imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, '
            'PRIMARY KEY (snapshot_date, ticker))'
        )
    )
    conn.execute(
        text(
            f'CREATE INDEX IF NOT EXISTS idx_sms_date ON '
            f'{_SECTOR_SNAPSHOT_TABLE}(snapshot_date)'
        )
    )


def save_sector_snapshot(df: pd.DataFrame, snapshot_date: date) -> int:
    """UPSERT eines Sektor-Momentum-Snapshots. Liefert die Anzahl geschriebener
    Zeilen. Raised bei DB-Problemen - der Aufrufer zeigt eine UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if df.empty:
        return 0

    rows = df.to_dict("records")
    for row in rows:
        row["snapshot_date"] = snapshot_date

    with engine.begin() as conn:
        _ensure_sector_snapshot_table(conn)
        conn.execute(
            text(
                f'INSERT INTO {_SECTOR_SNAPSHOT_TABLE} '
                '(snapshot_date, ticker, "group", display_name, '
                'last_price, sma_50, sma_200, momentum) '
                'VALUES (:snapshot_date, :ticker, :group, :display_name, '
                ':last_price, :sma_50, :sma_200, :momentum) '
                'ON CONFLICT (snapshot_date, ticker) DO UPDATE SET '
                '"group" = EXCLUDED."group", '
                'display_name = EXCLUDED.display_name, '
                'last_price = EXCLUDED.last_price, '
                'sma_50 = EXCLUDED.sma_50, '
                'sma_200 = EXCLUDED.sma_200, '
                'momentum = EXCLUDED.momentum, '
                'imported_at = CURRENT_TIMESTAMP'
            ),
            rows,
        )
    return len(rows)


def load_sector_snapshots(limit_weeks: int = 12) -> pd.DataFrame:
    """Laedt die juengsten ``limit_weeks`` Snapshot-Datumswerte inkl. aller
    Ticker-Zeilen. Gibt bei DB-Fehlern oder fehlender Tabelle einen leeren
    DataFrame zurueck. Raised nie.
    """

    empty = pd.DataFrame(
        columns=[
            "snapshot_date",
            "ticker",
            "group",
            "display_name",
            "last_price",
            "sma_50",
            "sma_200",
            "momentum",
        ]
    )
    engine = get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_sector_snapshot_table(conn)
            return pd.read_sql(
                text(
                    f'SELECT snapshot_date, ticker, "group", display_name, '
                    'last_price, sma_50, sma_200, momentum '
                    f'FROM {_SECTOR_SNAPSHOT_TABLE} '
                    'WHERE snapshot_date IN ('
                    '  SELECT snapshot_date FROM ('
                    '    SELECT DISTINCT snapshot_date '
                    f'    FROM {_SECTOR_SNAPSHOT_TABLE} '
                    '    ORDER BY snapshot_date DESC LIMIT :n'
                    '  ) s'
                    ') '
                    'ORDER BY snapshot_date ASC, ticker ASC'
                ),
                conn,
                params={"n": int(limit_weeks)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Sektor-Snapshots fehlgeschlagen: %s", exc)
        return empty


def list_sector_snapshot_dates() -> list[tuple[date, int]]:
    """Liefert alle vorhandenen Snapshot-Daten mit der Zeilenanzahl je Datum,
    absteigend sortiert (neuestes zuerst). Bei DB-Fehler -> leere Liste."""

    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_sector_snapshot_table(conn)
            rows = conn.execute(
                text(
                    f"SELECT snapshot_date, COUNT(*) AS n "
                    f"FROM {_SECTOR_SNAPSHOT_TABLE} "
                    "GROUP BY snapshot_date "
                    "ORDER BY snapshot_date DESC"
                )
            ).fetchall()
    except SQLAlchemyError as exc:
        log.warning("Auflisten der Sektor-Snapshots fehlgeschlagen: %s", exc)
        return []
    return [(row[0], int(row[1])) for row in rows]


def delete_sector_snapshot(snapshot_date: date) -> int:
    """Loescht alle Ticker-Zeilen eines Datums. Liefert die Zahl der geloeschten
    Zeilen. Raised bei DB-Problemen - der Aufrufer zeigt eine UI-Warnung."""

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    with engine.begin() as conn:
        _ensure_sector_snapshot_table(conn)
        result = conn.execute(
            text(
                f"DELETE FROM {_SECTOR_SNAPSHOT_TABLE} "
                "WHERE snapshot_date = :d"
            ),
            {"d": snapshot_date},
        )
    return int(result.rowcount or 0)


def _ensure_sector_score_history_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SECTOR_SCORE_HISTORY_TABLE} ("
            "snapshot_date DATE NOT NULL, "
            "level TEXT NOT NULL, "
            "key TEXT NOT NULL, "
            "score DOUBLE PRECISION, "
            "ret_1m DOUBLE PRECISION, "
            "ret_12m DOUBLE PRECISION, "
            "mom_12_1 DOUBLE PRECISION, "
            "sma200_dist DOUBLE PRECISION, "
            "sma50_dist DOUBLE PRECISION, "
            "breadth_sma200 INTEGER, "
            "n INTEGER, "
            "imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (snapshot_date, level, key))"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_ssh_date ON "
            f"{_SECTOR_SCORE_HISTORY_TABLE}(snapshot_date)"
        )
    )


def save_sector_score_history(
    records: list[dict], snapshot_date: date
) -> int:
    """UPSERT der aggregierten Sektor-/Industrie-Scores für ein Snapshot-Datum.

    ``records`` ist eine Liste mit Dicts, die mindestens ``level`` und ``key``
    sowie die zu speichernden Kennzahlen enthalten. Felder, die nicht im
    Schema vorkommen, werden ignoriert. Raised bei DB-Problemen — der
    Aufrufer zeigt eine UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if not records:
        return 0

    cols = (
        "score",
        "ret_1m",
        "ret_12m",
        "mom_12_1",
        "sma200_dist",
        "sma50_dist",
        "breadth_sma200",
        "n",
    )
    rows: list[dict] = []
    for r in records:
        row = {
            "snapshot_date": snapshot_date,
            "level": str(r["level"]),
            "key": str(r["key"]),
        }
        for c in cols:
            val = r.get(c)
            if val is None or (isinstance(val, float) and pd.isna(val)):
                row[c] = None
            else:
                row[c] = float(val) if c != "breadth_sma200" and c != "n" else int(val)
        rows.append(row)

    with engine.begin() as conn:
        _ensure_sector_score_history_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_SECTOR_SCORE_HISTORY_TABLE} "
                "(snapshot_date, level, key, score, ret_1m, ret_12m, "
                "mom_12_1, sma200_dist, sma50_dist, breadth_sma200, n) "
                "VALUES (:snapshot_date, :level, :key, :score, :ret_1m, "
                ":ret_12m, :mom_12_1, :sma200_dist, :sma50_dist, "
                ":breadth_sma200, :n) "
                "ON CONFLICT (snapshot_date, level, key) DO UPDATE SET "
                "score = EXCLUDED.score, "
                "ret_1m = EXCLUDED.ret_1m, "
                "ret_12m = EXCLUDED.ret_12m, "
                "mom_12_1 = EXCLUDED.mom_12_1, "
                "sma200_dist = EXCLUDED.sma200_dist, "
                "sma50_dist = EXCLUDED.sma50_dist, "
                "breadth_sma200 = EXCLUDED.breadth_sma200, "
                "n = EXCLUDED.n, "
                "imported_at = CURRENT_TIMESTAMP"
            ),
            rows,
        )
    return len(rows)


def load_sector_score_history(limit_snapshots: int = 12) -> pd.DataFrame:
    """Laedt die ``limit_snapshots`` juengsten Snapshot-Datumswerte komplett.

    Liefert einen DataFrame mit Spalten ``snapshot_date, level, key, score, …``,
    aufsteigend nach Datum sortiert. Bei DB-Fehlern oder fehlender Tabelle
    wird ein leerer DataFrame zurueckgegeben. Raised nie.
    """

    empty = pd.DataFrame(
        columns=[
            "snapshot_date",
            "level",
            "key",
            "score",
            "ret_1m",
            "ret_12m",
            "mom_12_1",
            "sma200_dist",
            "sma50_dist",
            "breadth_sma200",
            "n",
        ]
    )
    engine = get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_sector_score_history_table(conn)
            df = pd.read_sql(
                text(
                    f"SELECT snapshot_date, level, key, score, ret_1m, "
                    "ret_12m, mom_12_1, sma200_dist, sma50_dist, "
                    "breadth_sma200, n "
                    f"FROM {_SECTOR_SCORE_HISTORY_TABLE} "
                    "WHERE snapshot_date IN ("
                    "  SELECT snapshot_date FROM ("
                    "    SELECT DISTINCT snapshot_date "
                    f"    FROM {_SECTOR_SCORE_HISTORY_TABLE} "
                    "    ORDER BY snapshot_date DESC LIMIT :n"
                    "  ) s"
                    ") "
                    "ORDER BY snapshot_date ASC, level ASC, key ASC"
                ),
                conn,
                params={"n": int(limit_snapshots)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Sektor-Score-Historie fehlgeschlagen: %s", exc)
        return empty
    if not df.empty:
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    return df


def _ensure_ms_portfolio_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_MS_PORTFOLIO_TABLE} ("
            "position INTEGER PRIMARY KEY, "
            "ticker TEXT NOT NULL, "
            "name TEXT, "
            "imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_ms_portfolio(df: pd.DataFrame) -> int:
    """Ersetzt den Inhalt von ``ms_portfolio`` durch ``df`` (Spalten
    ``ticker``, optional ``name``; Position = Zeilenreihenfolge). Raised bei
    DB-Problemen — der Aufrufer zeigt eine UI-Warnung. Liefert Zeilenzahl.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if df is None or df.empty:
        return 0

    rows = [
        {
            "position": i,
            "ticker": str(r["ticker"]),
            "name": str(r.get("name") or "") or None,
        }
        for i, (_, r) in enumerate(df.iterrows())
        if isinstance(r.get("ticker"), str) and r.get("ticker")
    ]
    if not rows:
        return 0

    with engine.begin() as conn:
        _ensure_ms_portfolio_table(conn)
        conn.execute(text(f"DELETE FROM {_MS_PORTFOLIO_TABLE}"))
        conn.execute(
            text(
                f"INSERT INTO {_MS_PORTFOLIO_TABLE} (position, ticker, name) "
                "VALUES (:position, :ticker, :name)"
            ),
            rows,
        )
    return len(rows)


def load_ms_portfolio() -> pd.DataFrame | None:
    """Lädt das M&S-Portfolio (Spalten ``ticker, name, imported_at``,
    sortiert nach Position). ``None`` bei fehlender Tabelle/DB. Raised nie.
    """

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_ms_portfolio_table(conn)
            df = pd.read_sql(
                text(
                    f"SELECT ticker, name, imported_at FROM {_MS_PORTFOLIO_TABLE} "
                    "ORDER BY position ASC"
                ),
                conn,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden des M&S-Portfolios fehlgeschlagen: %s", exc)
        return None
    if df.empty:
        return None
    df["name"] = df["name"].fillna("")
    return df


_SIGNAL_HISTORY_COLS: tuple[str, ...] = (
    "momentum",
    "trend_phase",
    "last_price",
    "sma_20",
    "sma_50",
    "sma_200",
    "ret_1m",
    "mom_12_1",
    "dist_52w_high",
    "total_score",
)


def _ensure_signal_history_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SIGNAL_HISTORY_TABLE} ("
            "snapshot_date DATE NOT NULL, "
            "ticker TEXT NOT NULL, "
            "momentum TEXT NOT NULL, "
            "trend_phase TEXT, "
            "last_price DOUBLE PRECISION, "
            "sma_20 DOUBLE PRECISION, "
            "sma_50 DOUBLE PRECISION, "
            "sma_200 DOUBLE PRECISION, "
            "ret_1m DOUBLE PRECISION, "
            "mom_12_1 DOUBLE PRECISION, "
            "dist_52w_high DOUBLE PRECISION, "
            "total_score DOUBLE PRECISION, "
            "imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (snapshot_date, ticker))"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_ush_date ON "
            f"{_SIGNAL_HISTORY_TABLE}(snapshot_date)"
        )
    )


def save_signal_history(df: pd.DataFrame, snapshot_date: date) -> int:
    """UPSERT der SMA-Signal-Zustände je Aktie für ein Snapshot-Datum.

    ``df`` braucht mindestens ``ticker`` und ``momentum`` (kanonischer
    ``classify_momentum``-State); weitere Kennzahlen-Spalten aus
    ``_SIGNAL_HISTORY_COLS`` werden mitgespeichert, fehlende als NULL.
    Raised bei DB-Problemen — der Aufrufer zeigt eine UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if df is None or df.empty:
        return 0

    rows: list[dict] = []
    for _, r in df.iterrows():
        ticker = r.get("ticker")
        momentum = r.get("momentum")
        if not isinstance(ticker, str) or not ticker or not isinstance(momentum, str):
            continue
        row: dict = {"snapshot_date": snapshot_date, "ticker": ticker}
        for c in _SIGNAL_HISTORY_COLS:
            val = r.get(c)
            if c in ("momentum", "trend_phase"):
                row[c] = str(val) if isinstance(val, str) else None
            elif val is None or (isinstance(val, float) and pd.isna(val)):
                row[c] = None
            else:
                try:
                    row[c] = float(val)
                except (TypeError, ValueError):
                    row[c] = None
        rows.append(row)
    if not rows:
        return 0

    update_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in _SIGNAL_HISTORY_COLS)
    with engine.begin() as conn:
        _ensure_signal_history_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_SIGNAL_HISTORY_TABLE} "
                f"(snapshot_date, ticker, {', '.join(_SIGNAL_HISTORY_COLS)}) "
                f"VALUES (:snapshot_date, :ticker, "
                f"{', '.join(':' + c for c in _SIGNAL_HISTORY_COLS)}) "
                "ON CONFLICT (snapshot_date, ticker) DO UPDATE SET "
                f"{update_cols}, imported_at = CURRENT_TIMESTAMP"
            ),
            rows,
        )
    return len(rows)


def load_signal_history(limit_snapshots: int = 26) -> pd.DataFrame:
    """Lädt die ``limit_snapshots`` jüngsten Signal-Snapshots komplett.

    Liefert einen DataFrame mit ``snapshot_date, ticker, momentum, …``,
    aufsteigend nach Datum sortiert. Bei DB-Fehlern oder fehlender Tabelle
    wird ein leerer DataFrame zurückgegeben. Raised nie.
    """

    empty = pd.DataFrame(
        columns=["snapshot_date", "ticker", *_SIGNAL_HISTORY_COLS]
    )
    engine = get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_signal_history_table(conn)
            df = pd.read_sql(
                text(
                    f"SELECT snapshot_date, ticker, "
                    f"{', '.join(_SIGNAL_HISTORY_COLS)} "
                    f"FROM {_SIGNAL_HISTORY_TABLE} "
                    "WHERE snapshot_date IN ("
                    "  SELECT snapshot_date FROM ("
                    "    SELECT DISTINCT snapshot_date "
                    f"    FROM {_SIGNAL_HISTORY_TABLE} "
                    "    ORDER BY snapshot_date DESC LIMIT :n"
                    "  ) s"
                    ") "
                    "ORDER BY snapshot_date ASC, ticker ASC"
                ),
                conn,
                params={"n": int(limit_snapshots)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Signal-Historie fehlgeschlagen: %s", exc)
        return empty
    if not df.empty:
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    return df


def _settings_to_dict(s: Settings) -> dict:
    return {name: getattr(s, name) for name in _SETTINGS_FIELDS}


def _apply_settings_dict(s: Settings, payload: dict) -> None:
    """Spiegelt persistierte Werte auf ein Settings-Objekt. Unbekannte Keys
    werden ignoriert, fehlende Keys behalten den Default."""

    field_types = {f.name: f.type for f in dataclass_fields(Settings)}
    for key in _SETTINGS_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if field_types.get(key) == "dict[str, float]" and isinstance(value, dict):
            value = {k: float(v) for k, v in value.items()}
        setattr(s, key, value)


def _ensure_settings_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SETTINGS_TABLE} ("
            "id INTEGER PRIMARY KEY, "
            "data TEXT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_settings(settings: Settings) -> None:
    """UPSERT der App-Einstellungen als JSON (single-row, id=1).

    Raised bei DB-Problemen - der Aufrufer zeigt eine UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    payload = json.dumps(_settings_to_dict(settings))
    with engine.begin() as conn:
        _ensure_settings_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_SETTINGS_TABLE} (id, data, updated_at) "
                "VALUES (1, :data, CURRENT_TIMESTAMP) "
                "ON CONFLICT (id) DO UPDATE SET "
                "data = EXCLUDED.data, updated_at = EXCLUDED.updated_at"
            ),
            {"data": payload},
        )


def _ensure_factor_timing_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_FACTOR_TIMING_TABLE} ("
            "id INTEGER PRIMARY KEY, "
            "data TEXT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_factor_timing_inputs(values: dict) -> None:
    """UPSERT der Factor-Timing-Eingaben als JSON (single-row, id=1).

    Nur Felder aus ``_FACTOR_TIMING_FIELDS`` werden gespeichert; alles andere
    wird ignoriert. ``None``-Werte werden mitgeschrieben, damit eine
    versehentlich geleerte Zelle nicht beim nächsten Laden wieder den Default
    bekommt. Raised bei DB-Problemen — der Aufrufer entscheidet, ob die UI
    eine Warnung zeigt.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    cleaned: dict[str, float | None] = {}
    for key in _FACTOR_TIMING_FIELDS:
        if key not in values:
            continue
        v = values[key]
        if v is None:
            cleaned[key] = None
            continue
        try:
            cleaned[key] = float(v)
        except (TypeError, ValueError):
            cleaned[key] = None

    payload = json.dumps(cleaned)
    with engine.begin() as conn:
        _ensure_factor_timing_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_FACTOR_TIMING_TABLE} (id, data, updated_at) "
                "VALUES (1, :data, CURRENT_TIMESTAMP) "
                "ON CONFLICT (id) DO UPDATE SET "
                "data = EXCLUDED.data, updated_at = EXCLUDED.updated_at"
            ),
            {"data": payload},
        )


def load_factor_timing_inputs() -> dict | None:
    """Liefert die zuletzt gespeicherten Factor-Timing-Eingaben oder ``None``.

    Gibt ein Dict ausschließlich mit Keys aus ``_FACTOR_TIMING_FIELDS`` zurück,
    Werte sind ``float`` oder ``None``. Bei DB-Fehlern oder unleserlichem JSON
    wird ``None`` zurückgegeben — der Aufrufer fällt dann auf Defaults zurück.
    Raised nie.
    """

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_factor_timing_table(conn)
            row = conn.execute(
                text(f"SELECT data FROM {_FACTOR_TIMING_TABLE} WHERE id = 1")
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden der Factor-Timing-Eingaben fehlgeschlagen: %s", exc)
        return None

    if row is None:
        return None
    data = row[0]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            log.warning("Factor-Timing-JSON unleserlich: %s", exc)
            return None
    if not isinstance(data, dict):
        return None

    out: dict[str, float | None] = {}
    for key in _FACTOR_TIMING_FIELDS:
        if key not in data:
            continue
        v = data[key]
        if v is None:
            out[key] = None
            continue
        try:
            out[key] = float(v)
        except (TypeError, ValueError):
            out[key] = None
    return out


def _ensure_agent_analyses_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_AGENT_ANALYSES_TABLE} ("
            "ticker TEXT NOT NULL, "
            "run_id TEXT NOT NULL, "
            "agents_ticker TEXT, "
            "in_universe INTEGER, "
            "analysis_date DATE, "
            "rating TEXT, "
            "executive_summary TEXT, "
            "reports_json TEXT, "
            "factor_context_json TEXT, "
            "provider TEXT, "
            "total_score DOUBLE PRECISION, "
            "classification TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "PRIMARY KEY (ticker, run_id))"
        )
    )
    conn.execute(
        text(
            f"CREATE INDEX IF NOT EXISTS idx_aa_ticker ON "
            f"{_AGENT_ANALYSES_TABLE}(ticker)"
        )
    )


_AGENT_ANALYSIS_COLS: tuple[str, ...] = (
    "agents_ticker",
    "in_universe",
    "analysis_date",
    "rating",
    "executive_summary",
    "reports_json",
    "factor_context_json",
    "provider",
    "total_score",
    "classification",
)


def save_agent_analysis(record: dict) -> None:
    """UPSERT einer abgeschlossenen Agenten-Tiefenanalyse.

    ``record`` braucht mindestens ``ticker`` und ``run_id``; ``reports`` und
    ``factor_context`` dürfen als Dicts übergeben werden und werden hier als
    JSON-Text abgelegt. Raised bei DB-Problemen — der Aufrufer zeigt eine
    UI-Warnung.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    row: dict = {
        "ticker": str(record["ticker"]),
        "run_id": str(record["run_id"]),
    }
    payload = dict(record)
    if isinstance(payload.get("reports"), dict):
        payload["reports_json"] = json.dumps(payload.pop("reports"), ensure_ascii=False)
    if isinstance(payload.get("factor_context"), dict):
        payload["factor_context_json"] = json.dumps(
            payload.pop("factor_context"), ensure_ascii=False
        )
    for c in _AGENT_ANALYSIS_COLS:
        val = payload.get(c)
        if val is None or (isinstance(val, float) and pd.isna(val)):
            row[c] = None
        elif c == "total_score":
            try:
                row[c] = float(val)
            except (TypeError, ValueError):
                row[c] = None
        elif c == "in_universe":
            row[c] = 1 if val else 0
        else:
            row[c] = str(val)

    update_cols = ", ".join(f"{c} = EXCLUDED.{c}" for c in _AGENT_ANALYSIS_COLS)
    with engine.begin() as conn:
        _ensure_agent_analyses_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_AGENT_ANALYSES_TABLE} "
                f"(ticker, run_id, {', '.join(_AGENT_ANALYSIS_COLS)}) "
                f"VALUES (:ticker, :run_id, "
                f"{', '.join(':' + c for c in _AGENT_ANALYSIS_COLS)}) "
                "ON CONFLICT (ticker, run_id) DO UPDATE SET "
                f"{update_cols}, created_at = CURRENT_TIMESTAMP"
            ),
            [row],
        )


def _decode_agent_row(row) -> dict:
    d = dict(row._mapping)
    for src, dst in (("reports_json", "reports"), ("factor_context_json", "factor_context")):
        raw = d.get(src)
        d[dst] = None
        if isinstance(raw, str) and raw:
            try:
                d[dst] = json.loads(raw)
            except json.JSONDecodeError:
                pass
    return d


def load_agent_analysis(ticker: str) -> dict | None:
    """Neueste gespeicherte Agenten-Analyse für einen Ticker (JSON dekodiert)
    oder ``None``. Raised nie."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_agent_analyses_table(conn)
            row = conn.execute(
                text(
                    f"SELECT * FROM {_AGENT_ANALYSES_TABLE} "
                    "WHERE ticker = :t ORDER BY created_at DESC, run_id DESC LIMIT 1"
                ),
                {"t": str(ticker)},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden der Agenten-Analyse fehlgeschlagen: %s", exc)
        return None
    if row is None:
        return None
    return _decode_agent_row(row)


def load_agent_ratings() -> pd.DataFrame:
    """Neueste Agenten-Bewertung je Ticker als DataFrame
    (``ticker, rating, created_at``). Leer bei DB-Fehlern. Raised nie."""

    empty = pd.DataFrame(columns=["ticker", "rating", "created_at"])
    engine = get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_agent_analyses_table(conn)
            df = pd.read_sql(
                text(
                    "SELECT a.ticker, a.rating, a.created_at "
                    f"FROM {_AGENT_ANALYSES_TABLE} a "
                    "JOIN ("
                    "  SELECT ticker, MAX(created_at) AS max_created "
                    f"  FROM {_AGENT_ANALYSES_TABLE} GROUP BY ticker"
                    ") m ON a.ticker = m.ticker AND a.created_at = m.max_created"
                ),
                conn,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Agenten-Bewertungen fehlgeschlagen: %s", exc)
        return empty
    return df.drop_duplicates(subset=["ticker"], keep="last")


def list_agent_analyses(limit: int = 100) -> pd.DataFrame:
    """Alle gespeicherten Agenten-Analysen (neueste zuerst, ohne Reports).
    Leer bei DB-Fehlern. Raised nie."""

    cols = [
        "ticker",
        "run_id",
        "agents_ticker",
        "in_universe",
        "rating",
        "provider",
        "total_score",
        "classification",
        "factor_context_json",
        "created_at",
    ]
    empty = pd.DataFrame(columns=cols)
    engine = get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_agent_analyses_table(conn)
            return pd.read_sql(
                text(
                    f"SELECT {', '.join(cols)} FROM {_AGENT_ANALYSES_TABLE} "
                    "ORDER BY created_at DESC, run_id DESC LIMIT :n"
                ),
                conn,
                params={"n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Auflisten der Agenten-Analysen fehlgeschlagen: %s", exc)
        return empty


def _ensure_ticker_mappings_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_TICKER_MAPPINGS_TABLE} ("
            "source_ticker TEXT PRIMARY KEY, "
            "agents_ticker TEXT NOT NULL, "
            "confirmed_by_user INTEGER NOT NULL DEFAULT 0, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_ticker_mapping(
    source_ticker: str, agents_ticker: str, confirmed: bool = True
) -> None:
    """UPSERT eines Koyfin→Yahoo-Ticker-Mappings. Raised bei DB-Problemen."""

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    with engine.begin() as conn:
        _ensure_ticker_mappings_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_TICKER_MAPPINGS_TABLE} "
                "(source_ticker, agents_ticker, confirmed_by_user, updated_at) "
                "VALUES (:s, :a, :c, CURRENT_TIMESTAMP) "
                "ON CONFLICT (source_ticker) DO UPDATE SET "
                "agents_ticker = EXCLUDED.agents_ticker, "
                "confirmed_by_user = EXCLUDED.confirmed_by_user, "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {"s": str(source_ticker), "a": str(agents_ticker), "c": 1 if confirmed else 0},
        )


def load_ticker_mapping(source_ticker: str) -> str | None:
    """Gespeichertes Mapping für einen Koyfin-Ticker oder ``None``. Raised nie."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_ticker_mappings_table(conn)
            row = conn.execute(
                text(
                    f"SELECT agents_ticker FROM {_TICKER_MAPPINGS_TABLE} "
                    "WHERE source_ticker = :s"
                ),
                {"s": str(source_ticker)},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden des Ticker-Mappings fehlgeschlagen: %s", exc)
        return None
    return row[0] if row else None


def load_settings() -> Settings | None:
    """Laedt die zuletzt gespeicherten Einstellungen. Gibt ``None`` zurueck,
    wenn keine vorhanden sind oder die DB nicht erreichbar ist. Raised nie."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_settings_table(conn)
            row = conn.execute(
                text(f"SELECT data FROM {_SETTINGS_TABLE} WHERE id = 1")
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden der Einstellungen fehlgeschlagen: %s", exc)
        return None

    if row is None:
        return None
    data = row[0]
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as exc:
            log.warning("Einstellungs-JSON unleserlich: %s", exc)
            return None
    if not isinstance(data, dict):
        return None

    settings = Settings()
    _apply_settings_dict(settings, data)
    return settings
