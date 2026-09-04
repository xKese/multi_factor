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
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import Date, DateTime, create_engine, inspect, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import SQLAlchemyError

from .config import Settings

log = logging.getLogger(__name__)

_DEFAULT_URL = "sqlite:///data/multifactor.db"
_UNIVERSE_TABLE = "koyfin_universe"
_UNIVERSE_HISTORY_TABLE = "koyfin_universe_history"
_META_TABLE = "koyfin_meta"

# Ablageort der unveränderten Roh-CSVs des PIT-Archivs. Über die
# Umgebungsvariable kann der Pfad (z. B. für Tests oder Docker-Volumes)
# umgelenkt werden; gelesen wird sie bei jedem Aufruf, nicht beim Import.
_ARCHIVE_DIR_ENV = "KOYFIN_ARCHIVE_DIR"
_DEFAULT_ARCHIVE_DIR = os.path.join("data", "archive")
_SECTOR_SNAPSHOT_TABLE = "sector_momentum_snapshots"
_SECTOR_SCORE_HISTORY_TABLE = "sector_score_history"
_SIGNAL_HISTORY_TABLE = "universe_signal_history"
_MS_PORTFOLIO_TABLE = "ms_portfolio"
_SETTINGS_TABLE = "app_settings"
_FACTOR_TIMING_TABLE = "factor_timing_inputs"
_AGENT_ANALYSES_TABLE = "agent_analyses"
_TICKER_MAPPINGS_TABLE = "ticker_mappings"
_FACTOR_TIMING_HISTORY_TABLE = "factor_timing_history"

# Vollständige Liste der Eingabefelder der Factor-Timing-Seite (Makro-,
# Sentiment- und Faktor-Momentum-Werte). Wird beim Persistieren als JSON-Text
# abgelegt; unbekannte Keys werden beim Laden ignoriert, fehlende kehren als
# ``None`` zurück (Aufrufer mergt mit Defaults).
# ``flows`` (Fund Flows) wurde entfernt: das Signal floss nie in eine Regel
# ein und hat keine verlässliche Datenquelle. Alte Persistenz-Payloads mit
# dem Key werden beim Laden schlicht ignoriert.
_FACTOR_TIMING_FIELDS: tuple[str, ...] = (
    "pmi",
    "pmi_trend",
    "cli",
    "spread",
    "cpi",
    "vix",
    "credit",
    "pcr",
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
    "buy_threshold",
    "sell_threshold",
    "min_stocks_per_industry",
    "min_factor_coverage",
    "min_total_coverage",
    "percentile_mode",
    "agents_provider",
    "agents_quick_model",
    "agents_deep_model",
    "agents_depth",
    "agents_language",
    "agents_temperature",
    "agents_prev_analysis",
    # Risiko & Benchmark. Hinweis: ``_apply_settings_dict`` mergt nur
    # ``dict[str, float]``-Felder mit den Defaults — gespeicherte
    # Szenario-/Schock-Dicts gewinnen als Ganzes (dokumentiert im README).
    "risk_benchmark_symbol",
    "risk_report_dir",
    "risk_av_requests_per_minute",
    "risk_benchmark_sector_weights",
    "risk_scenario_windows",
    "risk_factor_shocks",
    # Composite v2 und Portfoliokonstruktion (Spec Composite v2).
    "scoring_version",
    "factor_timing_mode",
    "v2_weight_value",
    "v2_weight_quality",
    "v2_weight_momentum",
    "v2_weight_investment",
    "v2_min_factor_weight",
    "v2_min_group_size",
    "v2_min_group_valid",
    "v2_winsor_lower",
    "v2_winsor_upper",
    "v2_zscore_cap",
    "v2_composite_winsor_lower",
    "v2_composite_winsor_upper",
    "v2_min_volatility",
    "v2_min_valid_nonfin",
    "v2_min_valid_financial",
    "filter_min_market_cap",
    "filter_min_piotroski",
    "filter_min_altman",
    "filter_min_adv",
    "filter_min_coverage",
    "filter_min_listing_days",
    "filter_max_de",
    "filter_min_icr",
    "pc_target_n",
    "pc_min_n",
    "pc_max_n",
    "pc_entry_pct",
    "pc_exit_pct",
    "pc_fill_pct",
    "pc_sector_band",
    "pc_region_band",
    "pc_max_per_sector",
    "pc_benchmark_source",
    "pc_benchmark_max_age_days",
    "risk_benchmark_sector_weights_asof",
    "pc_vol_floor",
    "pc_vol_cap",
    "pc_weight_cap",
    "pc_weight_floor",
    "pc_te_target_low",
    "pc_te_target_high",
    "pc_te_max",
    "pc_max_cte_share",
    "pc_te_min_coverage",
    "pc_rebalance_months",
    "pc_interim_months",
    "pc_turnover_budget_full",
    "pc_turnover_budget_interim",
    "pc_min_trade_size",
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


def _snapshot_date_fallback(df: pd.DataFrame) -> date:
    """Snapshot-Datum aus ``export_date`` des Frames, Fallback Importdatum.

    Bewusst ohne Dateinamen-Fallback (der lebt in
    ``signal_events.snapshot_date_from_universe`` — ein Import hier wäre
    zirkulär); Aufrufer mit Dateinamen-Kontext übergeben ``snapshot_date``
    explizit. Die Datums-Plausibilisierung spiegelt
    ``signal_events.parse_export_dates``: numerische Werte würden von
    ``pd.to_datetime`` als Nanosekunden seit der Unix-Epoche
    fehlinterpretiert (→ 01.01.1970), unplausible Daten (vor 2000 oder
    weit in der Zukunft) werden verworfen.
    """
    if df is not None and "export_date" in df.columns:
        values = df["export_date"].where(
            df["export_date"].map(
                lambda v: isinstance(v, str) or hasattr(v, "year")
            )
        )
        parsed = pd.to_datetime(values, errors="coerce").dropna()
        if not parsed.empty:
            lower = pd.Timestamp("2000-01-01")
            upper = pd.Timestamp(date.today() + timedelta(days=7))
            parsed = parsed[(parsed >= lower) & (parsed <= upper)]
        if not parsed.empty:
            return parsed.max().date()
    return date.today()


def _sql_type_for(dtype) -> str:
    """DDL-Typ für ``ALTER TABLE ADD COLUMN`` im SQLite∩Postgres-Dialekt."""
    if pd.api.types.is_bool_dtype(dtype):
        return "BOOLEAN"
    if pd.api.types.is_integer_dtype(dtype):
        return "BIGINT"
    if pd.api.types.is_float_dtype(dtype):
        return "DOUBLE PRECISION"
    if pd.api.types.is_datetime64_any_dtype(dtype):
        return "TIMESTAMP"
    return "TEXT"


def _archive_universe_snapshot(conn, df: pd.DataFrame, snapshot_date: date) -> None:
    """Schreibt ``df`` als Punkt-in-Zeit-Snapshot in die Historientabelle.

    Semantik: UPSERT auf Snapshot-Ebene — existieren bereits Zeilen mit
    demselben ``snapshot_date``, wird genau dieser Snapshot ersetzt
    (DELETE + Append in derselben Transaktion); ältere Snapshots bleiben
    unangetastet. Die Tabelle entsteht beim ersten Import (bestehende
    Datenbanken werden so ohne Datenverlust erweitert); neue Spalten
    späterer Exporte/Scorings werden per ``ALTER TABLE`` nachgerüstet,
    Altbestände tragen dort NULL.
    """
    frame = df.copy()
    if "uid" not in frame.columns:
        # Bestands-Frames vor Einführung der uid-Spalte (analog STATE.set_raw).
        from .uid import assign_uids

        frame = assign_uids(frame)
    # Listen-/Dict-Spalten (z. B. ``filter_reasons`` aus Composite v2) sind
    # nicht SQL-fähig — als JSON-Text ablegen.
    for col in frame.columns:
        if frame[col].dtype == object and frame[col].map(
            lambda v: isinstance(v, (list, dict))
        ).any():
            frame[col] = frame[col].map(
                lambda v: json.dumps(v, ensure_ascii=False)
                if isinstance(v, (list, dict))
                else v
            )
    frame["snapshot_date"] = snapshot_date
    frame["imported_at"] = datetime.now(timezone.utc).replace(tzinfo=None)

    insp = inspect(conn)
    if insp.has_table(_UNIVERSE_HISTORY_TABLE):
        existing = {c["name"] for c in insp.get_columns(_UNIVERSE_HISTORY_TABLE)}
        for col in frame.columns:
            if col not in existing:
                conn.execute(
                    text(
                        f'ALTER TABLE {_UNIVERSE_HISTORY_TABLE} '
                        f'ADD COLUMN "{col}" {_sql_type_for(frame[col].dtype)}'
                    )
                )
        conn.execute(
            text(
                f"DELETE FROM {_UNIVERSE_HISTORY_TABLE} "
                "WHERE snapshot_date = :d"
            ),
            {"d": snapshot_date},
        )

    frame.to_sql(
        _UNIVERSE_HISTORY_TABLE,
        conn,
        if_exists="append",
        index=False,
        dtype={"snapshot_date": Date(), "imported_at": DateTime()},
    )
    # Unique-Constraint (snapshot_date, uid) — als Unique-Index, damit die
    # von pandas erzeugte Tabelle nachträglich abgesichert werden kann
    # (identische Syntax in SQLite und Postgres).
    conn.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_kuh_snapshot_uid "
            f"ON {_UNIVERSE_HISTORY_TABLE} (snapshot_date, uid)"
        )
    )


def archive_raw_csv(raw: bytes, snapshot_date: date) -> str | None:
    """Legt die unveränderte Roh-CSV unter
    ``data/archive/koyfin_<snapshot_date>.csv`` ab (bestehende Datei wird
    überschrieben). Fail-open: Liefert den Pfad oder ``None`` bei Fehler —
    ein Dateisystemproblem darf den Import nicht blockieren.
    """
    try:
        archive_dir = os.getenv(_ARCHIVE_DIR_ENV, _DEFAULT_ARCHIVE_DIR)
        os.makedirs(archive_dir, exist_ok=True)
        path = os.path.join(
            archive_dir, f"koyfin_{snapshot_date.isoformat()}.csv"
        )
        with open(path, "wb") as fh:
            fh.write(raw)
        return path
    except OSError as exc:
        log.warning("Roh-CSV-Archivierung fehlgeschlagen: %s", exc)
        return None


def save_universe(
    df: pd.DataFrame,
    *,
    snapshot_date: date | None = None,
    archive_df: pd.DataFrame | None = None,
    raw_csv: bytes | None = None,
) -> None:
    """Ersetzt den Inhalt von ``koyfin_universe`` durch ``df`` und aktualisiert
    die Meta-Zeile. Raised bei Fehler — der Aufrufer zeigt eine UI-Warnung.

    Zusätzlich (PIT-Archiv) wird in derselben Transaktion ein
    Punkt-in-Zeit-Snapshot nach ``koyfin_universe_history`` geschrieben:
    ``archive_df`` (typisch das gescorte Universum — Rohkennzahlen plus
    berechnete Scores), Fallback ``df``. ``snapshot_date`` ist das
    Export-Datum des CSV; ohne Angabe wird es aus ``export_date`` des Frames
    abgeleitet, Fallback ist das Importdatum. Ist ``raw_csv`` gesetzt, wird
    die unveränderte Roh-CSV unter ``data/archive/`` abgelegt (fail-open).
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    snap = snapshot_date or _snapshot_date_fallback(df)
    hist = archive_df if archive_df is not None and not archive_df.empty else df

    with engine.begin() as conn:
        _archive_universe_snapshot(conn, hist, snap)
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

    if raw_csv is not None:
        archive_raw_csv(raw_csv, snap)


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


def _coerce_snapshot_date(value) -> date | None:
    """SQLite liefert DATE-Spalten als ISO-String, Postgres als ``date``."""
    if isinstance(value, date):
        return value
    try:
        return pd.to_datetime(value).date()
    except (ValueError, TypeError):
        return None


def load_universe_snapshot(snapshot_date: date) -> pd.DataFrame | None:
    """Lädt einen archivierten Punkt-in-Zeit-Snapshot des Universums.

    Gibt ``None`` zurück, wenn das Datum nicht archiviert ist, die Tabelle
    fehlt oder die DB nicht erreichbar ist. Raised nie.
    """
    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            if not inspect(conn).has_table(_UNIVERSE_HISTORY_TABLE):
                return None
            df = pd.read_sql(
                text(
                    f"SELECT * FROM {_UNIVERSE_HISTORY_TABLE} "
                    "WHERE snapshot_date = :d"
                ),
                conn,
                params={"d": snapshot_date},
            )
        return df if not df.empty else None
    except SQLAlchemyError as exc:
        log.warning("Laden des Universum-Snapshots fehlgeschlagen: %s", exc)
        return None


def list_snapshots() -> list[tuple[date, int]]:
    """Liefert alle archivierten Universum-Snapshots als
    ``(snapshot_date, zeilenanzahl)``, absteigend sortiert (neuestes zuerst).
    Bei DB-Fehler oder fehlender Tabelle → leere Liste. Raised nie.
    """
    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            if not inspect(conn).has_table(_UNIVERSE_HISTORY_TABLE):
                return []
            rows = conn.execute(
                text(
                    f"SELECT snapshot_date, COUNT(*) AS n "
                    f"FROM {_UNIVERSE_HISTORY_TABLE} "
                    "GROUP BY snapshot_date "
                    "ORDER BY snapshot_date DESC"
                )
            ).fetchall()
    except SQLAlchemyError as exc:
        log.warning("Auflisten der Universum-Snapshots fehlgeschlagen: %s", exc)
        return []
    result: list[tuple[date, int]] = []
    for row in rows:
        parsed = _coerce_snapshot_date(row[0])
        if parsed is not None:
            result.append((parsed, int(row[1])))
    return result


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


def load_sector_score_history(max_age_days: int = 400) -> pd.DataFrame:
    """Laedt alle Snapshots der letzten ``max_age_days`` Tage (relativ zum
    juengsten Snapshot, nicht zur Wall-Clock).

    Datumbasiert statt Anzahl-basiert, damit das Fenster unabhaengig von der
    Upload-Frequenz ist: Bei taeglichen Uploads deckten die frueher geladenen
    "12 juengsten Snapshot-Daten" nur ~2 Wochen ab und der ~30-Tage-Lookup
    fuer den Delta-Score lief ins Leere.

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
            latest = conn.execute(
                text(
                    f"SELECT MAX(snapshot_date) FROM {_SECTOR_SCORE_HISTORY_TABLE}"
                )
            ).scalar()
            if latest is None:
                return empty
            latest_date = pd.Timestamp(latest).date()
            cutoff = latest_date - timedelta(days=int(max_age_days))
            df = pd.read_sql(
                text(
                    f"SELECT snapshot_date, level, key, score, ret_1m, "
                    "ret_12m, mom_12_1, sma200_dist, sma50_dist, "
                    "breadth_sma200, n "
                    f"FROM {_SECTOR_SCORE_HISTORY_TABLE} "
                    "WHERE snapshot_date >= :cutoff "
                    "ORDER BY snapshot_date ASC, level ASC, key ASC"
                ),
                conn,
                params={"cutoff": cutoff},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Sektor-Score-Historie fehlgeschlagen: %s", exc)
        return empty
    if not df.empty:
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    return df


def _ensure_column(conn, table: str, column: str, ddl_type: str) -> None:
    """Idempotentes ``ALTER TABLE … ADD COLUMN`` im SQLite∩Postgres-Dialekt.

    Bewusst per Inspector-Check statt „ALTER versuchen und Fehler
    verschlucken": In Postgres bricht ein fehlgeschlagenes Statement die
    gesamte laufende Transaktion ab (``InFailedSqlTransaction``) — alle
    Folge-Statements im selben ``engine.begin()``-Block würden scheitern.
    """

    existing = {c["name"] for c in inspect(conn).get_columns(table)}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _ensure_ms_portfolio_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_MS_PORTFOLIO_TABLE} ("
            "position INTEGER PRIMARY KEY, "
            "ticker TEXT NOT NULL, "
            "name TEXT, "
            "weight DOUBLE PRECISION, "
            "imported_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    # Migration für Bestands-DBs: Die Tabelle wird per DELETE+INSERT
    # wiederbefüllt, aber nie gedroppt — Altbestände haben die Spalte nicht.
    _ensure_column(conn, _MS_PORTFOLIO_TABLE, "weight", "DOUBLE PRECISION")


def save_ms_portfolio(df: pd.DataFrame) -> int:
    """Ersetzt den Inhalt von ``ms_portfolio`` durch ``df`` (Spalten
    ``ticker``, optional ``name`` und ``weight``; Position =
    Zeilenreihenfolge). Raised bei DB-Problemen — der Aufrufer zeigt eine
    UI-Warnung. Liefert Zeilenzahl.
    """

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    if df is None or df.empty:
        return 0

    has_weight = "weight" in df.columns
    rows = []
    for i, (_, r) in enumerate(df.iterrows()):
        if not (isinstance(r.get("ticker"), str) and r.get("ticker")):
            continue
        weight = r.get("weight") if has_weight else None
        rows.append(
            {
                "position": i,
                "ticker": str(r["ticker"]),
                "name": str(r.get("name") or "") or None,
                "weight": None if weight is None or pd.isna(weight) else float(weight),
            }
        )
    if not rows:
        return 0

    with engine.begin() as conn:
        _ensure_ms_portfolio_table(conn)
        conn.execute(text(f"DELETE FROM {_MS_PORTFOLIO_TABLE}"))
        conn.execute(
            text(
                f"INSERT INTO {_MS_PORTFOLIO_TABLE} "
                "(position, ticker, name, weight) "
                "VALUES (:position, :ticker, :name, :weight)"
            ),
            rows,
        )
    return len(rows)


def load_ms_portfolio() -> pd.DataFrame | None:
    """Lädt das M&S-Portfolio (Spalten ``ticker, name, weight, imported_at``,
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
                    f"SELECT ticker, name, weight, imported_at "
                    f"FROM {_MS_PORTFOLIO_TABLE} "
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
    werden ignoriert, fehlende Keys behalten den Default.

    Gewichts-Dicts werden mit den Defaults gemergt: Indikatoren, die nach dem
    Speichern der Einstellungen neu ins Modell gekommen sind, fehlen im
    persistierten Dict und erhalten ihr Default-Gewicht — sonst blieben sie
    für Bestandsnutzer dauerhaft unsichtbar/ungescort. Explizit auf 0 gesetzte
    Gewichte bleiben erhalten (Key vorhanden). Die Gewichte werden im Scoring
    ohnehin auf ihre Summe normiert, ein Merge verschiebt also nur relative
    Anteile, keine Skala."""

    field_types = {f.name: f.type for f in dataclass_fields(Settings)}
    for key in _SETTINGS_FIELDS:
        if key not in payload:
            continue
        value = payload[key]
        if field_types.get(key) == "dict[str, float]" and isinstance(value, dict):
            defaults = getattr(s, key, {})
            stored = {k: float(v) for k, v in value.items()}
            value = {**defaults, **stored} if isinstance(defaults, dict) else stored
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


def _ensure_factor_timing_history_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_FACTOR_TIMING_HISTORY_TABLE} ("
            "snapshot_date DATE PRIMARY KEY, "
            "regime TEXT NOT NULL, "
            "weights_json TEXT NOT NULL, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_factor_timing_snapshot(
    snapshot_date: date, regime: str, weights: dict[str, float]
) -> None:
    """UPSERT der Regime-/Gewichts-Entscheidung eines Tages (Timeline auf
    der Factor-Timing-Seite). Raised bei DB-Problemen — der Aufrufer
    entscheidet, ob die UI eine Warnung zeigt."""

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")

    payload = json.dumps({str(k): float(v) for k, v in weights.items()})
    with engine.begin() as conn:
        _ensure_factor_timing_history_table(conn)
        conn.execute(
            text(
                f"INSERT INTO {_FACTOR_TIMING_HISTORY_TABLE} "
                "(snapshot_date, regime, weights_json, updated_at) "
                "VALUES (:d, :r, :w, CURRENT_TIMESTAMP) "
                "ON CONFLICT (snapshot_date) DO UPDATE SET "
                "regime = EXCLUDED.regime, "
                "weights_json = EXCLUDED.weights_json, "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {"d": snapshot_date, "r": str(regime), "w": payload},
        )


def load_factor_timing_history(limit: int = 30) -> list[dict]:
    """Jüngste Regime-Snapshots, neueste zuerst: Liste von Dicts mit
    ``snapshot_date`` (date), ``regime`` (str), ``weights`` (dict).
    Leer bei DB-Fehlern. Raised nie."""

    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_factor_timing_history_table(conn)
            rows = conn.execute(
                text(
                    f"SELECT snapshot_date, regime, weights_json "
                    f"FROM {_FACTOR_TIMING_HISTORY_TABLE} "
                    "ORDER BY snapshot_date DESC LIMIT :n"
                ),
                {"n": int(limit)},
            ).fetchall()
    except SQLAlchemyError as exc:
        log.warning("Laden der Factor-Timing-Historie fehlgeschlagen: %s", exc)
        return []

    out: list[dict] = []
    for row in rows:
        weights: dict = {}
        raw = row[2]
        if isinstance(raw, str) and raw:
            try:
                weights = json.loads(raw)
            except json.JSONDecodeError:
                weights = {}
        out.append(
            {
                "snapshot_date": pd.Timestamp(row[0]).date(),
                "regime": str(row[1]),
                "weights": weights,
            }
        )
    return out


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


# ── Composite v2 / Portfoliokonstruktion (Spec 8, 5.3, 10) ──────────────

_OVERRIDE_TABLE = "override_register"
_REGION_WEIGHTS_TABLE = "risk_benchmark_region_weights"
_MODEL_PORTFOLIO_TABLE = "model_portfolio"
_MODEL_PORTFOLIO_META_TABLE = "model_portfolio_meta"

# Maximale Override-Laufzeit (Spec 8): expires_at ≤ created_at + 180 Tage.
OVERRIDE_MAX_DAYS = 180
OVERRIDE_MIN_REASON_LEN = 20
_OVERRIDE_DIRECTIONS = ("exclude", "include", "weight")


def _ensure_override_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_OVERRIDE_TABLE} ("
            "id INTEGER PRIMARY KEY, "
            "uid TEXT NOT NULL, "
            "direction TEXT NOT NULL CHECK (direction IN "
            "('exclude', 'include', 'weight')), "
            "target_weight DOUBLE PRECISION, "
            f"reason TEXT NOT NULL CHECK (length(reason) >= {OVERRIDE_MIN_REASON_LEN}), "
            "owner TEXT NOT NULL, "
            "created_at TIMESTAMP NOT NULL, "
            "expires_at DATE NOT NULL, "
            "status TEXT NOT NULL DEFAULT 'active' CHECK (status IN "
            "('active', 'expired', 'closed')), "
            "closed_at TIMESTAMP, "
            "closed_by TEXT, "
            "close_note TEXT)"
        )
    )


def save_override(
    uid: str,
    direction: str,
    reason: str,
    owner: str,
    expires_at: date,
    target_weight: float | None = None,
) -> int:
    """Legt einen Override an (Spec 8). Validiert Pflichtfelder und raised
    ``ValueError`` bei Regelverletzung, ``RuntimeError`` ohne DB-Engine.
    Liefert die vergebene Override-ID."""

    if not (isinstance(uid, str) and uid.strip()):
        raise ValueError("Override ohne Titel (uid) ist nicht anlegbar.")
    if direction not in _OVERRIDE_DIRECTIONS:
        raise ValueError(f"Unbekannte Override-Richtung: {direction!r}")
    if not (isinstance(reason, str) and len(reason.strip()) >= OVERRIDE_MIN_REASON_LEN):
        raise ValueError(
            "Override-Begründung ist Pflicht und braucht mindestens "
            f"{OVERRIDE_MIN_REASON_LEN} Zeichen."
        )
    if not (isinstance(owner, str) and owner.strip()):
        raise ValueError("Override ohne Verantwortlichen (owner) ist nicht anlegbar.")
    if expires_at is None:
        raise ValueError("Override ohne Ablaufdatum ist nicht anlegbar.")
    created_at = datetime.now(timezone.utc).replace(tzinfo=None)
    if expires_at > created_at.date() + timedelta(days=OVERRIDE_MAX_DAYS):
        raise ValueError(
            f"Ablaufdatum liegt mehr als {OVERRIDE_MAX_DAYS} Tage in der Zukunft."
        )
    if direction == "weight":
        if target_weight is None or not (0.0 < float(target_weight) <= 1.0):
            raise ValueError(
                "Weight-Override braucht ein Zielgewicht in (0, 1]."
            )
    elif target_weight is not None:
        raise ValueError("target_weight ist nur bei direction='weight' zulässig.")

    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    with engine.begin() as conn:
        _ensure_override_table(conn)
        next_id = conn.execute(
            text(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {_OVERRIDE_TABLE}")
        ).scalar_one()
        conn.execute(
            text(
                f"INSERT INTO {_OVERRIDE_TABLE} "
                "(id, uid, direction, target_weight, reason, owner, "
                "created_at, expires_at, status) "
                "VALUES (:id, :uid, :direction, :target_weight, :reason, "
                ":owner, :created_at, :expires_at, 'active')"
            ),
            {
                "id": int(next_id),
                "uid": uid.strip(),
                "direction": direction,
                "target_weight": (
                    None if target_weight is None else float(target_weight)
                ),
                "reason": reason.strip(),
                "owner": owner.strip(),
                "created_at": created_at,
                "expires_at": expires_at,
            },
        )
    return int(next_id)


def close_override(override_id: int, closed_by: str, close_note: str = "") -> None:
    """Schließt einen Override manuell. Raised bei DB-Problemen."""
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    with engine.begin() as conn:
        _ensure_override_table(conn)
        conn.execute(
            text(
                f"UPDATE {_OVERRIDE_TABLE} SET status = 'closed', "
                "closed_at = CURRENT_TIMESTAMP, closed_by = :by, "
                "close_note = :note WHERE id = :id"
            ),
            {"by": closed_by, "note": close_note or None, "id": int(override_id)},
        )


def expire_overrides(snapshot_date: date) -> list[dict]:
    """Setzt abgelaufene Overrides (``expires_at < snapshot_date``) auf
    ``expired`` (Spec 8) und liefert die betroffenen Einträge für die
    Diagnose ("Override abgelaufen – erneuern oder schließen").
    Fail-open: leere Liste bei DB-Problemen."""

    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_override_table(conn)
            rows = conn.execute(
                text(
                    f"SELECT id, uid, direction FROM {_OVERRIDE_TABLE} "
                    "WHERE status = 'active' AND expires_at < :snap"
                ),
                {"snap": snapshot_date},
            ).fetchall()
            if rows:
                conn.execute(
                    text(
                        f"UPDATE {_OVERRIDE_TABLE} SET status = 'expired' "
                        "WHERE status = 'active' AND expires_at < :snap"
                    ),
                    {"snap": snapshot_date},
                )
    except SQLAlchemyError as exc:
        log.warning("Override-Ablaufprüfung fehlgeschlagen: %s", exc)
        return []
    return [
        {"id": r[0], "uid": r[1], "direction": r[2]} for r in rows
    ]


def load_overrides(status: str | None = None) -> pd.DataFrame | None:
    """Lädt das Override-Register (optional nach Status gefiltert).
    Fail-open: ``None`` bei DB-Problemen."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_override_table(conn)
            query = f"SELECT * FROM {_OVERRIDE_TABLE}"
            params: dict = {}
            if status:
                query += " WHERE status = :status"
                params["status"] = status
            df = pd.read_sql(text(query + " ORDER BY id ASC"), conn, params=params)
    except SQLAlchemyError as exc:
        log.warning("Laden des Override-Registers fehlgeschlagen: %s", exc)
        return None
    return df


def _ensure_region_weights_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_REGION_WEIGHTS_TABLE} ("
            "region TEXT PRIMARY KEY, "
            "weight DOUBLE PRECISION NOT NULL, "
            "asof DATE NOT NULL)"
        )
    )


def save_region_weights(weights: dict[str, float], asof: date) -> None:
    """Ersetzt die Benchmark-Regionsgewichte (Spec 5.3). Raised bei Fehler."""
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    with engine.begin() as conn:
        _ensure_region_weights_table(conn)
        conn.execute(text(f"DELETE FROM {_REGION_WEIGHTS_TABLE}"))
        if weights:
            conn.execute(
                text(
                    f"INSERT INTO {_REGION_WEIGHTS_TABLE} "
                    "(region, weight, asof) VALUES (:region, :weight, :asof)"
                ),
                [
                    {"region": str(r), "weight": float(w), "asof": asof}
                    for r, w in weights.items()
                ],
            )


def load_region_weights() -> tuple[dict[str, float], date | None]:
    """Lädt die Benchmark-Regionsgewichte. Fail-open: ({}, None)."""
    engine = get_engine()
    if engine is None:
        return {}, None
    try:
        with engine.begin() as conn:
            _ensure_region_weights_table(conn)
            rows = conn.execute(
                text(f"SELECT region, weight, asof FROM {_REGION_WEIGHTS_TABLE}")
            ).fetchall()
    except SQLAlchemyError as exc:
        log.warning("Laden der Regionsgewichte fehlgeschlagen: %s", exc)
        return {}, None
    weights = {str(r[0]): float(r[1]) for r in rows}
    asof = _coerce_snapshot_date(rows[0][2]) if rows else None
    return weights, asof


def settings_hash_v2(settings: Settings) -> str:
    """SHA-256 über die JSON-serialisierten, sortierten v2- und pc-Settings
    (Spec 10) — ordnet jede Portfolioversion ihrer Parametrisierung zu."""
    import hashlib

    payload = {
        f.name: getattr(settings, f.name)
        for f in dataclass_fields(settings)
        if f.name.startswith(("v2_", "pc_", "filter_"))
        or f.name in ("scoring_version", "factor_timing_mode")
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


_MODEL_PORTFOLIO_COLS: tuple[str, ...] = (
    "uid",
    "composite_z",
    "composite_pct",
    "zone_v2",
    "weight_model",
    "weight_effective",
    "cte",
    "action",
    "reason",
    "rebalance_mode",
    "override_id",
)


def _ensure_model_portfolio_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_MODEL_PORTFOLIO_TABLE} ("
            "snapshot_date DATE NOT NULL, "
            "uid TEXT NOT NULL, "
            "composite_z DOUBLE PRECISION, "
            "composite_pct DOUBLE PRECISION, "
            "zone_v2 TEXT, "
            "weight_model DOUBLE PRECISION, "
            "weight_effective DOUBLE PRECISION, "
            "cte DOUBLE PRECISION, "
            "action TEXT, "
            "reason TEXT, "
            "rebalance_mode TEXT, "
            "override_id INTEGER, "
            "PRIMARY KEY (snapshot_date, uid))"
        )
    )


def _ensure_model_portfolio_meta_table(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_MODEL_PORTFOLIO_META_TABLE} ("
            "snapshot_date DATE PRIMARY KEY, "
            "rebalance_mode TEXT, "
            "n_titles INTEGER, "
            "te_ex_ante DOUBLE PRECISION, "
            "te_coverage DOUBLE PRECISION, "
            "turnover_oneway DOUBLE PRECISION, "
            "n_trades INTEGER, "
            "n_deferred INTEGER, "
            "settings_hash TEXT, "
            "diagnostics TEXT, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def save_model_portfolio(
    df: pd.DataFrame, meta: dict, snapshot_date: date
) -> None:
    """Persistiert Zielportfolio und Metadaten (Spec 10).

    UPSERT wie im PIT-Archiv: Zeilen desselben ``snapshot_date`` werden
    ersetzt, ältere Snapshots bleiben unangetastet. Raised bei Fehler.
    """
    engine = get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    rows = []
    for _, r in df.iterrows():
        row = {"snapshot_date": snapshot_date}
        for col in _MODEL_PORTFOLIO_COLS:
            value = r.get(col)
            row[col] = None if value is None or pd.isna(value) else value
        if row["override_id"] is not None:
            row["override_id"] = int(row["override_id"])
        rows.append(row)
    with engine.begin() as conn:
        _ensure_model_portfolio_table(conn)
        _ensure_model_portfolio_meta_table(conn)
        conn.execute(
            text(
                f"DELETE FROM {_MODEL_PORTFOLIO_TABLE} "
                "WHERE snapshot_date = :snap"
            ),
            {"snap": snapshot_date},
        )
        if rows:
            cols = ", ".join(("snapshot_date",) + _MODEL_PORTFOLIO_COLS)
            binds = ", ".join(
                f":{c}" for c in ("snapshot_date",) + _MODEL_PORTFOLIO_COLS
            )
            conn.execute(
                text(
                    f"INSERT INTO {_MODEL_PORTFOLIO_TABLE} ({cols}) "
                    f"VALUES ({binds})"
                ),
                rows,
            )
        conn.execute(
            text(
                f"INSERT INTO {_MODEL_PORTFOLIO_META_TABLE} "
                "(snapshot_date, rebalance_mode, n_titles, te_ex_ante, "
                "te_coverage, turnover_oneway, n_trades, n_deferred, "
                "settings_hash, diagnostics, updated_at) "
                "VALUES (:snapshot_date, :rebalance_mode, :n_titles, "
                ":te_ex_ante, :te_coverage, :turnover_oneway, :n_trades, "
                ":n_deferred, :settings_hash, :diagnostics, "
                "CURRENT_TIMESTAMP) "
                "ON CONFLICT (snapshot_date) DO UPDATE SET "
                "rebalance_mode = EXCLUDED.rebalance_mode, "
                "n_titles = EXCLUDED.n_titles, "
                "te_ex_ante = EXCLUDED.te_ex_ante, "
                "te_coverage = EXCLUDED.te_coverage, "
                "turnover_oneway = EXCLUDED.turnover_oneway, "
                "n_trades = EXCLUDED.n_trades, "
                "n_deferred = EXCLUDED.n_deferred, "
                "settings_hash = EXCLUDED.settings_hash, "
                "diagnostics = EXCLUDED.diagnostics, "
                "updated_at = CURRENT_TIMESTAMP"
            ),
            {
                "snapshot_date": snapshot_date,
                "rebalance_mode": meta.get("rebalance_mode"),
                "n_titles": meta.get("n_titles"),
                "te_ex_ante": meta.get("te_ex_ante"),
                "te_coverage": meta.get("te_coverage"),
                "turnover_oneway": meta.get("turnover_oneway"),
                "n_trades": meta.get("n_trades"),
                "n_deferred": meta.get("n_deferred"),
                "settings_hash": meta.get("settings_hash"),
                "diagnostics": meta.get("diagnostics"),
            },
        )


def load_model_portfolio(snapshot_date: date | None = None) -> pd.DataFrame | None:
    """Lädt das Zielportfolio (neuester Snapshot, wenn kein Datum gegeben).
    Fail-open: ``None``."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_model_portfolio_table(conn)
            snap = snapshot_date
            if snap is None:
                row = conn.execute(
                    text(
                        f"SELECT MAX(snapshot_date) FROM {_MODEL_PORTFOLIO_TABLE}"
                    )
                ).fetchone()
                snap = _coerce_snapshot_date(row[0]) if row else None
            if snap is None:
                return None
            df = pd.read_sql(
                text(
                    f"SELECT * FROM {_MODEL_PORTFOLIO_TABLE} "
                    "WHERE snapshot_date = :snap ORDER BY uid ASC"
                ),
                conn,
                params={"snap": snap},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden des Modellportfolios fehlgeschlagen: %s", exc)
        return None
    if df.empty:
        return None
    df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
    return df


def list_model_portfolio_dates() -> list[date]:
    """Alle Snapshot-Daten des Modellportfolios, neueste zuerst. Fail-open."""
    engine = get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_model_portfolio_meta_table(conn)
            rows = conn.execute(
                text(
                    f"SELECT snapshot_date FROM {_MODEL_PORTFOLIO_META_TABLE} "
                    "ORDER BY snapshot_date DESC"
                )
            ).fetchall()
    except SQLAlchemyError as exc:
        log.warning("Auflisten der Modellportfolio-Daten fehlgeschlagen: %s", exc)
        return []
    return [d for d in (_coerce_snapshot_date(r[0]) for r in rows) if d is not None]


def load_model_portfolio_meta(snapshot_date: date | None = None) -> dict | None:
    """Lädt die Metadaten eines Modellportfolio-Snapshots (neuester ohne
    Datum). Fail-open: ``None``."""

    engine = get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_model_portfolio_meta_table(conn)
            if snapshot_date is None:
                row = conn.execute(
                    text(
                        f"SELECT * FROM {_MODEL_PORTFOLIO_META_TABLE} "
                        "ORDER BY snapshot_date DESC LIMIT 1"
                    )
                ).mappings().fetchone()
            else:
                row = conn.execute(
                    text(
                        f"SELECT * FROM {_MODEL_PORTFOLIO_META_TABLE} "
                        "WHERE snapshot_date = :snap"
                    ),
                    {"snap": snapshot_date},
                ).mappings().fetchone()
    except SQLAlchemyError as exc:
        log.warning("Laden der Modellportfolio-Metadaten fehlgeschlagen: %s", exc)
        return None
    if row is None:
        return None
    meta = dict(row)
    meta["snapshot_date"] = _coerce_snapshot_date(meta.get("snapshot_date"))
    return meta
