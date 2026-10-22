"""DB-Persistenz des KI-Analysten (gleicher Dialekt-Rahmen wie ``persistence``:
SQLite ∩ Postgres, IDs per ``MAX(id)+1`` in der Transaktion).

Tabellen:

- ``analyst_runs`` — ein Eintrag je Lauf (Status, Budget, Report-Pfad).
- ``analyst_events`` — erkannte Ereignisse je Titel, dedupliziert über
  ``dedupe_key`` (``uid|kind|period``).
- ``analyst_insights`` — LLM-Ergebnisse (Transkript-Zusammenfassung,
  News-Digest, Synthese, Themen, Trends) als JSON; ``insight_key`` macht
  den Upsert dialektneutral.
- ``analyst_suggestions`` — Vorschläge je Titel mit Status (open/done/
  dismissed).
- ``analyst_themes`` — Themen-Cluster je Lauf (Universumsebene).
- ``analyst_company_links`` — Unternehmensverknüpfungen (Zulieferer,
  Kunden, Wettbewerber) aus Calls/News, je Lauf.
- ``analyst_trends`` / ``analyst_trend_links`` — Trends und ihre
  Profiteure je Lauf.
- ``analyst_watch_state`` — Cursor je Titel (was wurde zuletzt gesehen).

Lesefunktionen sind fail-open (leeres Ergebnis bei DB-Fehlern),
Schreibfunktionen raisen.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import persistence

log = logging.getLogger(__name__)

_RUNS = "analyst_runs"
_EVENTS = "analyst_events"
_INSIGHTS = "analyst_insights"
_SUGGESTIONS = "analyst_suggestions"
_THEMES = "analyst_themes"
_COMPANY_LINKS = "analyst_company_links"
_TRENDS = "analyst_trends"
_TREND_LINKS = "analyst_trend_links"
_WATCH = "analyst_watch_state"
_CHAT_SESSIONS = "analyst_chat_sessions"
_CHAT_MESSAGES = "analyst_chat_messages"

SUGGESTION_STATUSES = ("open", "done", "dismissed")
CHAT_ROLES = ("user", "assistant", "tool")


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def _engine():
    engine = persistence.get_engine()
    if engine is None:
        raise RuntimeError("Datenbank-Engine nicht verfügbar")
    return engine


def _json(value) -> str | None:
    if value is None:
        return None
    if is_dataclass(value) and not isinstance(value, type):
        value = asdict(value)
    return json.dumps(value, ensure_ascii=False, default=_json_default)


def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    if hasattr(obj, "item"):
        try:
            return obj.item()
        except Exception:  # noqa: BLE001
            pass
    return str(obj)


def _loads(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        return json.loads(value)
    except ValueError:
        return None


def _ts(value) -> datetime | None:
    """TIMESTAMP-Spalte → datetime (SQLite liefert Strings)."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        try:
            return pd.to_datetime(value).to_pydatetime()
        except Exception:  # noqa: BLE001
            return None


def _d(value) -> date | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _next_id(conn, table: str) -> int:
    row = conn.execute(text(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}")).fetchone()
    return int(row[0]) if row else 1


def _rows(conn, sql: str, params: dict | None = None) -> list[dict]:
    result = conn.execute(text(sql), params or {})
    return [dict(r._mapping) for r in result.fetchall()]


# --------------------------------------------------------------------------- #
# DDL
# --------------------------------------------------------------------------- #
def _ensure_runs(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_RUNS} ("
            "run_id TEXT PRIMARY KEY, "
            "trigger TEXT, scope TEXT, model TEXT, status TEXT, "
            "dry_run INTEGER NOT NULL DEFAULT 0, "
            "started_at TIMESTAMP, finished_at TIMESTAMP, "
            "n_companies INTEGER, n_events INTEGER, n_llm_calls INTEGER, "
            "n_av_calls INTEGER, n_themes INTEGER, n_trends INTEGER, "
            "report_path TEXT, diagnostics_json TEXT, error TEXT)"
        )
    )


def _ensure_events(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_EVENTS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT NOT NULL, ticker TEXT, "
            "kind TEXT NOT NULL, period TEXT, event_date DATE, title TEXT, "
            "payload_json TEXT, dedupe_key TEXT NOT NULL UNIQUE, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_ae_uid ON {_EVENTS}(uid)"))


def _ensure_insights(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_INSIGHTS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT, ticker TEXT, "
            "kind TEXT NOT NULL, period TEXT, status TEXT, model TEXT, "
            "content_json TEXT, raw_text TEXT, prompt_hash TEXT, n_calls INTEGER, "
            "insight_key TEXT NOT NULL UNIQUE, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_ai_uid ON {_INSIGHTS}(uid)"))


def _ensure_suggestions(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_SUGGESTIONS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT, uid TEXT NOT NULL, ticker TEXT, "
            "suggestion_type TEXT, confidence DOUBLE PRECISION, rationale TEXT, "
            "evidence_json TEXT, quant_zone TEXT, quant_score DOUBLE PRECISION, "
            "quant_consistency TEXT, n_interplay INTEGER, n_unpriced INTEGER, "
            "status TEXT NOT NULL DEFAULT 'open', "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "resolved_at TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_as_uid ON {_SUGGESTIONS}(uid)"))


def _ensure_themes(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_THEMES} ("
            "id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, title TEXT, description TEXT, "
            "kind TEXT, direction TEXT, confidence DOUBLE PRECISION, uids_json TEXT, "
            "sectors_json TEXT, regions_json TEXT, evidence_json TEXT, "
            "confirmation TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_at_run ON {_THEMES}(run_id)"))


def _ensure_company_links(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_COMPANY_LINKS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, uid TEXT NOT NULL, ticker TEXT, "
            "related_uid TEXT, related_name TEXT, related_ticker TEXT, "
            "relation TEXT, context TEXT, source TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_acl_uid ON {_COMPANY_LINKS}(uid)"))
    conn.execute(
        text(f"CREATE INDEX IF NOT EXISTS idx_acl_rel ON {_COMPANY_LINKS}(related_uid)")
    )


def _ensure_trends(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_TRENDS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, trend_idx INTEGER, "
            "title TEXT, description TEXT, kind TEXT, horizon TEXT, "
            "strength INTEGER, confidence DOUBLE PRECISION, evidence_json TEXT, "
            "industry_groups_json TEXT, loser_note TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_atr_run ON {_TRENDS}(run_id)"))
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_TREND_LINKS} ("
            "id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, trend_id INTEGER NOT NULL, "
            "uid TEXT NOT NULL, ticker TEXT, role TEXT, pricing_llm TEXT, "
            "pricing_quant TEXT, action TEXT, reasoning_json TEXT, evidence_json TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_atl_uid ON {_TREND_LINKS}(uid)"))


def _ensure_watch(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_WATCH} ("
            "uid TEXT PRIMARY KEY, av_symbol TEXT, "
            "last_earnings_report_date DATE, last_transcript_quarter TEXT, "
            "last_insider_date DATE, last_news_at TIMESTAMP, last_zone TEXT, "
            "last_score DOUBLE PRECISION, last_z_quality DOUBLE PRECISION, "
            "last_checked_at TIMESTAMP, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )


def _ensure_chat_sessions(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_CHAT_SESSIONS} ("
            "id INTEGER PRIMARY KEY, title TEXT, context_uid TEXT, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP, "
            "n_messages INTEGER NOT NULL DEFAULT 0)"
        )
    )


def _ensure_chat_messages(conn) -> None:
    conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {_CHAT_MESSAGES} ("
            "id INTEGER PRIMARY KEY, session_id INTEGER NOT NULL, role TEXT NOT NULL, "
            "content TEXT, tool_name TEXT, tool_calls_json TEXT, trace_json TEXT, model TEXT, "
            "n_llm_calls INTEGER, n_av_calls INTEGER, "
            "created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP)"
        )
    )
    conn.execute(text(f"CREATE INDEX IF NOT EXISTS idx_acm_session ON {_CHAT_MESSAGES}(session_id)"))


def ensure_tables() -> None:
    """Legt alle Tabellen an (idempotent). Raised bei DB-Problemen."""
    with _engine().begin() as conn:
        for fn in (
            _ensure_runs,
            _ensure_events,
            _ensure_insights,
            _ensure_suggestions,
            _ensure_themes,
            _ensure_company_links,
            _ensure_trends,
            _ensure_watch,
            _ensure_chat_sessions,
            _ensure_chat_messages,
        ):
            fn(conn)


# --------------------------------------------------------------------------- #
# Läufe
# --------------------------------------------------------------------------- #
_RUN_COLS = (
    "trigger", "scope", "model", "status", "dry_run", "started_at", "finished_at",
    "n_companies", "n_events", "n_llm_calls", "n_av_calls", "n_themes", "n_trends",
    "report_path", "diagnostics_json", "error",
)


def save_run(record: dict) -> None:
    """INSERT/UPSERT einer Lauf-Zeile (``run_id`` Pflicht)."""
    row = {c: record.get(c) for c in _RUN_COLS}
    row["run_id"] = str(record["run_id"])
    row["dry_run"] = 1 if record.get("dry_run") else 0
    if isinstance(record.get("diagnostics"), list):
        row["diagnostics_json"] = _json(record["diagnostics"])
    update = ", ".join(f"{c} = EXCLUDED.{c}" for c in _RUN_COLS)
    with _engine().begin() as conn:
        _ensure_runs(conn)
        conn.execute(
            text(
                f"INSERT INTO {_RUNS} (run_id, {', '.join(_RUN_COLS)}) "
                f"VALUES (:run_id, {', '.join(':' + c for c in _RUN_COLS)}) "
                f"ON CONFLICT (run_id) DO UPDATE SET {update}"
            ),
            [row],
        )


def update_run(run_id: str, **fields) -> None:
    """Teilupdate einer Lauf-Zeile (nur bekannte Spalten)."""
    if isinstance(fields.get("diagnostics"), list):
        fields["diagnostics_json"] = _json(fields.pop("diagnostics"))
    if "dry_run" in fields:
        fields["dry_run"] = 1 if fields["dry_run"] else 0
    cols = {k: v for k, v in fields.items() if k in _RUN_COLS}
    if not cols:
        return
    sets = ", ".join(f"{c} = :{c}" for c in cols)
    with _engine().begin() as conn:
        _ensure_runs(conn)
        conn.execute(
            text(f"UPDATE {_RUNS} SET {sets} WHERE run_id = :run_id"),
            {**cols, "run_id": str(run_id)},
        )


def _decode_run(row: dict) -> dict:
    row["started_at"] = _ts(row.get("started_at"))
    row["finished_at"] = _ts(row.get("finished_at"))
    row["dry_run"] = bool(row.get("dry_run"))
    return row


def load_run(run_id: str) -> dict | None:
    engine = persistence.get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_runs(conn)
            rows = _rows(conn, f"SELECT * FROM {_RUNS} WHERE run_id = :r", {"r": str(run_id)})
    except SQLAlchemyError as exc:
        log.warning("Laden des Analyst-Laufs fehlgeschlagen: %s", exc)
        return None
    return _decode_run(rows[0]) if rows else None


def list_runs(limit: int = 20) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_runs(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_RUNS} ORDER BY run_id DESC LIMIT :n",
                {"n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Analyst-Läufe fehlgeschlagen: %s", exc)
        return []
    return [_decode_run(r) for r in rows]


# --------------------------------------------------------------------------- #
# Ereignisse
# --------------------------------------------------------------------------- #
def save_events(events: list, run_id: str | None = None) -> int:
    """Speichert Ereignisse (Dataclass oder Dict); liefert die Zahl der
    *neuen* Zeilen (bekannte ``dedupe_key`` werden übersprungen)."""
    recs = []
    for ev in events:
        d = asdict(ev) if is_dataclass(ev) and not isinstance(ev, type) else dict(ev)
        recs.append(d)
    if not recs:
        return 0
    with _engine().begin() as conn:
        _ensure_events(conn)
        keys = [str(r["dedupe_key"]) for r in recs]
        existing: set[str] = set()
        for i in range(0, len(keys), 200):
            chunk = keys[i : i + 200]
            placeholders = ", ".join(f":k{j}" for j in range(len(chunk)))
            found = conn.execute(
                text(f"SELECT dedupe_key FROM {_EVENTS} WHERE dedupe_key IN ({placeholders})"),
                {f"k{j}": k for j, k in enumerate(chunk)},
            ).fetchall()
            existing.update(str(r[0]) for r in found)
        new = 0
        next_id = _next_id(conn, _EVENTS)
        for r in recs:
            key = str(r["dedupe_key"])
            if key in existing:
                continue
            existing.add(key)
            conn.execute(
                text(
                    f"INSERT INTO {_EVENTS} (id, run_id, uid, ticker, kind, period, "
                    "event_date, title, payload_json, dedupe_key) VALUES "
                    "(:id, :run_id, :uid, :ticker, :kind, :period, :event_date, "
                    ":title, :payload_json, :dedupe_key)"
                ),
                {
                    "id": next_id,
                    "run_id": run_id or r.get("run_id"),
                    "uid": str(r["uid"]),
                    "ticker": r.get("ticker"),
                    "kind": str(r["kind"]),
                    "period": r.get("period"),
                    "event_date": _d(r.get("event_date")),
                    "title": r.get("title"),
                    "payload_json": _json(r.get("payload")),
                    "dedupe_key": key,
                },
            )
            next_id += 1
            new += 1
    return new


def _decode_event(row: dict) -> dict:
    row["payload"] = _loads(row.pop("payload_json", None)) or {}
    row["event_date"] = _d(row.get("event_date"))
    row["created_at"] = _ts(row.get("created_at"))
    return row


def list_events(limit: int = 200, uid: str | None = None, kind: str | None = None) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    where, params = [], {"n": int(limit)}
    if uid:
        where.append("uid = :uid")
        params["uid"] = str(uid)
    if kind:
        where.append("kind = :kind")
        params["kind"] = str(kind)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    try:
        with engine.begin() as conn:
            _ensure_events(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_EVENTS} {clause} ORDER BY id DESC LIMIT :n",
                params,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Analyst-Ereignisse fehlgeschlagen: %s", exc)
        return []
    return [_decode_event(r) for r in rows]


# --------------------------------------------------------------------------- #
# Insights
# --------------------------------------------------------------------------- #
def insight_key(run_id: str | None, kind: str, uid: str | None, period: str | None) -> str:
    return f"{run_id or ''}|{kind}|{uid or ''}|{period or ''}"


_INSIGHT_COLS = (
    "run_id", "uid", "ticker", "kind", "period", "status", "model",
    "content_json", "raw_text", "prompt_hash", "n_calls",
)


def save_insight(record: dict) -> None:
    """UPSERT eines Insights (``kind`` Pflicht; ``content`` als Dict)."""
    row = {c: record.get(c) for c in _INSIGHT_COLS}
    if isinstance(record.get("content"), (dict, list)):
        row["content_json"] = _json(record["content"])
    row["insight_key"] = record.get("insight_key") or insight_key(
        record.get("run_id"), str(record["kind"]), record.get("uid"), record.get("period")
    )
    update = ", ".join(f"{c} = EXCLUDED.{c}" for c in _INSIGHT_COLS)
    with _engine().begin() as conn:
        _ensure_insights(conn)
        row["id"] = _next_id(conn, _INSIGHTS)
        conn.execute(
            text(
                f"INSERT INTO {_INSIGHTS} (id, insight_key, {', '.join(_INSIGHT_COLS)}) "
                f"VALUES (:id, :insight_key, {', '.join(':' + c for c in _INSIGHT_COLS)}) "
                f"ON CONFLICT (insight_key) DO UPDATE SET {update}, "
                "created_at = CURRENT_TIMESTAMP"
            ),
            [row],
        )


def _decode_insight(row: dict) -> dict:
    row["content"] = _loads(row.pop("content_json", None))
    row["created_at"] = _ts(row.get("created_at"))
    return row


def has_insight(uid: str, kind: str, period: str) -> bool:
    """Gibt es bereits ein erfolgreich geparstes Insight (über alle Läufe)?"""
    engine = persistence.get_engine()
    if engine is None:
        return False
    try:
        with engine.begin() as conn:
            _ensure_insights(conn)
            row = conn.execute(
                text(
                    f"SELECT 1 FROM {_INSIGHTS} WHERE uid = :u AND kind = :k "
                    "AND period = :p AND status = 'ok' LIMIT 1"
                ),
                {"u": str(uid), "k": str(kind), "p": str(period)},
            ).fetchone()
    except SQLAlchemyError as exc:
        log.warning("Insight-Prüfung fehlgeschlagen: %s", exc)
        return False
    return row is not None


def load_insight(uid: str, kind: str, period: str | None = None) -> dict | None:
    """Neuestes Insight (``kind``) eines Titels, optional je Periode."""
    engine = persistence.get_engine()
    if engine is None:
        return None
    params = {"u": str(uid), "k": str(kind)}
    extra = ""
    if period is not None:
        extra = " AND period = :p"
        params["p"] = str(period)
    try:
        with engine.begin() as conn:
            _ensure_insights(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_INSIGHTS} WHERE uid = :u AND kind = :k{extra} "
                "ORDER BY id DESC LIMIT 1",
                params,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden des Insights fehlgeschlagen: %s", exc)
        return None
    return _decode_insight(rows[0]) if rows else None


def list_insights(
    uid: str | None = None,
    kind: str | None = None,
    run_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    where, params = [], {"n": int(limit)}
    if uid:
        where.append("uid = :uid")
        params["uid"] = str(uid)
    if kind:
        where.append("kind = :kind")
        params["kind"] = str(kind)
    if run_id:
        where.append("run_id = :run_id")
        params["run_id"] = str(run_id)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    try:
        with engine.begin() as conn:
            _ensure_insights(conn)
            rows = _rows(
                conn, f"SELECT * FROM {_INSIGHTS} {clause} ORDER BY id DESC LIMIT :n", params
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Insights fehlgeschlagen: %s", exc)
        return []
    return [_decode_insight(r) for r in rows]


def load_latest_synthesis(uid: str) -> dict | None:
    """Neueste erfolgreich geparste Synthese eines Titels."""
    engine = persistence.get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_insights(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_INSIGHTS} WHERE uid = :u AND kind = 'synthesis' "
                "AND status = 'ok' ORDER BY id DESC LIMIT 1",
                {"u": str(uid)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Synthese fehlgeschlagen: %s", exc)
        return None
    return _decode_insight(rows[0]) if rows else None


# --------------------------------------------------------------------------- #
# Vorschläge
# --------------------------------------------------------------------------- #
_SUGG_COLS = (
    "run_id", "uid", "ticker", "suggestion_type", "confidence", "rationale",
    "evidence_json", "quant_zone", "quant_score", "quant_consistency",
    "n_interplay", "n_unpriced", "status",
)


def save_suggestion(record: dict) -> int:
    row = {c: record.get(c) for c in _SUGG_COLS}
    if isinstance(record.get("evidence"), (list, dict)):
        row["evidence_json"] = _json(record["evidence"])
    row["status"] = record.get("status") or "open"
    row["uid"] = str(record["uid"])
    with _engine().begin() as conn:
        _ensure_suggestions(conn)
        row["id"] = _next_id(conn, _SUGGESTIONS)
        conn.execute(
            text(
                f"INSERT INTO {_SUGGESTIONS} (id, {', '.join(_SUGG_COLS)}) "
                f"VALUES (:id, {', '.join(':' + c for c in _SUGG_COLS)})"
            ),
            [row],
        )
    return int(row["id"])


def _decode_suggestion(row: dict) -> dict:
    row["evidence"] = _loads(row.pop("evidence_json", None)) or []
    row["created_at"] = _ts(row.get("created_at"))
    row["resolved_at"] = _ts(row.get("resolved_at"))
    return row


def list_suggestions(
    limit: int = 100,
    run_id: str | None = None,
    uid: str | None = None,
    status: str | None = None,
) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    where, params = [], {"n": int(limit)}
    if run_id:
        where.append("run_id = :run_id")
        params["run_id"] = str(run_id)
    if uid:
        where.append("uid = :uid")
        params["uid"] = str(uid)
    if status:
        where.append("status = :status")
        params["status"] = str(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    try:
        with engine.begin() as conn:
            _ensure_suggestions(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_SUGGESTIONS} {clause} ORDER BY id DESC LIMIT :n",
                params,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Vorschläge fehlgeschlagen: %s", exc)
        return []
    return [_decode_suggestion(r) for r in rows]


def load_latest_suggestions(open_only: bool = True) -> pd.DataFrame:
    """Neuester Vorschlag je uid als DataFrame (``uid, ticker,
    suggestion_type, confidence, rationale, status, created_at, id``)."""
    cols = ["id", "uid", "ticker", "suggestion_type", "confidence", "rationale", "status", "created_at"]
    empty = pd.DataFrame(columns=cols)
    engine = persistence.get_engine()
    if engine is None:
        return empty
    try:
        with engine.begin() as conn:
            _ensure_suggestions(conn)
            df = pd.read_sql(
                text(
                    f"SELECT s.{', s.'.join(cols)} FROM {_SUGGESTIONS} s "
                    f"JOIN (SELECT uid, MAX(id) AS max_id FROM {_SUGGESTIONS} GROUP BY uid) m "
                    "ON s.uid = m.uid AND s.id = m.max_id"
                ),
                conn,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Vorschläge fehlgeschlagen: %s", exc)
        return empty
    if open_only and not df.empty:
        df = df[df["status"] == "open"]
    return df.reset_index(drop=True)


def set_suggestion_status(suggestion_id: int, status: str) -> None:
    if status not in SUGGESTION_STATUSES:
        raise ValueError(f"Unbekannter Status: {status}")
    resolved = None if status == "open" else datetime.now()
    with _engine().begin() as conn:
        _ensure_suggestions(conn)
        conn.execute(
            text(
                f"UPDATE {_SUGGESTIONS} SET status = :s, resolved_at = :r WHERE id = :id"
            ),
            {"s": status, "r": resolved, "id": int(suggestion_id)},
        )


# --------------------------------------------------------------------------- #
# Themen (Run-Ebene)
# --------------------------------------------------------------------------- #
def save_themes(run_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with _engine().begin() as conn:
        _ensure_themes(conn)
        next_id = _next_id(conn, _THEMES)
        for t in rows:
            conn.execute(
                text(
                    f"INSERT INTO {_THEMES} (id, run_id, title, description, kind, direction, "
                    "confidence, uids_json, sectors_json, regions_json, evidence_json, "
                    "confirmation) VALUES (:id, :run_id, :title, :description, :kind, "
                    ":direction, :confidence, :uids_json, :sectors_json, :regions_json, "
                    ":evidence_json, :confirmation)"
                ),
                {
                    "id": next_id,
                    "run_id": str(run_id),
                    "title": t.get("title"),
                    "description": t.get("description"),
                    "kind": t.get("kind"),
                    "direction": t.get("direction"),
                    "confidence": t.get("confidence"),
                    "uids_json": _json(t.get("uids") or []),
                    "sectors_json": _json(t.get("sectors") or []),
                    "regions_json": _json(t.get("regions") or []),
                    "evidence_json": _json(t.get("evidence") or []),
                    "confirmation": t.get("confirmation"),
                },
            )
            next_id += 1
    return len(rows)


def _decode_theme(row: dict) -> dict:
    for src, dst in (
        ("uids_json", "uids"), ("sectors_json", "sectors"),
        ("regions_json", "regions"), ("evidence_json", "evidence"),
    ):
        row[dst] = _loads(row.pop(src, None)) or []
    row["created_at"] = _ts(row.get("created_at"))
    return row


def _latest_run_with_rows(conn, table: str) -> str | None:
    row = conn.execute(text(f"SELECT MAX(run_id) FROM {table}")).fetchone()
    return str(row[0]) if row and row[0] else None


def list_themes(run_id: str | None = None, limit: int = 50) -> list[dict]:
    """Themen eines Laufs; ohne ``run_id`` der neueste Lauf mit Themen."""
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_themes(conn)
            rid = run_id or _latest_run_with_rows(conn, _THEMES)
            if not rid:
                return []
            rows = _rows(
                conn,
                f"SELECT * FROM {_THEMES} WHERE run_id = :r ORDER BY id LIMIT :n",
                {"r": str(rid), "n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Themen fehlgeschlagen: %s", exc)
        return []
    return [_decode_theme(r) for r in rows]


# --------------------------------------------------------------------------- #
# Unternehmensverknüpfungen
# --------------------------------------------------------------------------- #
def save_company_links(run_id: str, rows: list[dict]) -> int:
    if not rows:
        return 0
    with _engine().begin() as conn:
        _ensure_company_links(conn)
        next_id = _next_id(conn, _COMPANY_LINKS)
        for r in rows:
            if not r.get("uid"):
                continue
            conn.execute(
                text(
                    f"INSERT INTO {_COMPANY_LINKS} (id, run_id, uid, ticker, related_uid, "
                    "related_name, related_ticker, relation, context, source) VALUES "
                    "(:id, :run_id, :uid, :ticker, :related_uid, :related_name, "
                    ":related_ticker, :relation, :context, :source)"
                ),
                {
                    "id": next_id,
                    "run_id": str(run_id),
                    "uid": str(r["uid"]),
                    "ticker": r.get("ticker"),
                    "related_uid": r.get("related_uid"),
                    "related_name": r.get("related_name"),
                    "related_ticker": r.get("related_ticker"),
                    "relation": r.get("relation"),
                    "context": r.get("context"),
                    "source": r.get("source"),
                },
            )
            next_id += 1
    return len(rows)


def load_company_links_for(uid: str, limit: int = 50) -> list[dict]:
    """Verknüpfungen eines Titels in beide Richtungen (neueste zuerst)."""
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_company_links(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_COMPANY_LINKS} WHERE uid = :u OR related_uid = :u "
                "ORDER BY id DESC LIMIT :n",
                {"u": str(uid), "n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Unternehmensverknüpfungen fehlgeschlagen: %s", exc)
        return []
    out = []
    for r in rows:
        r["created_at"] = _ts(r.get("created_at"))
        r["direction"] = "out" if str(r.get("uid")) == str(uid) else "in"
        out.append(r)
    return out


def list_company_links(run_id: str | None = None, limit: int = 200) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_company_links(conn)
            rid = run_id or _latest_run_with_rows(conn, _COMPANY_LINKS)
            if not rid:
                return []
            rows = _rows(
                conn,
                f"SELECT * FROM {_COMPANY_LINKS} WHERE run_id = :r ORDER BY id LIMIT :n",
                {"r": str(rid), "n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Unternehmensverknüpfungen fehlgeschlagen: %s", exc)
        return []
    for r in rows:
        r["created_at"] = _ts(r.get("created_at"))
    return rows


# --------------------------------------------------------------------------- #
# Trends & Profiteure
# --------------------------------------------------------------------------- #
def save_trends(run_id: str, trends: list[dict], links: list[dict]) -> tuple[int, int]:
    """Speichert Trends und Profiteur-Links; ``links[i]["trend_idx"]``
    verweist auf den Index in ``trends``. Liefert ``(n_trends, n_links)``."""
    if not trends:
        return 0, 0
    with _engine().begin() as conn:
        _ensure_trends(conn)
        next_id = _next_id(conn, _TRENDS)
        id_by_idx: dict[int, int] = {}
        for idx, t in enumerate(trends):
            conn.execute(
                text(
                    f"INSERT INTO {_TRENDS} (id, run_id, trend_idx, title, description, kind, "
                    "horizon, strength, confidence, evidence_json, industry_groups_json, "
                    "loser_note) VALUES (:id, :run_id, :trend_idx, :title, :description, "
                    ":kind, :horizon, :strength, :confidence, :evidence_json, "
                    ":industry_groups_json, :loser_note)"
                ),
                {
                    "id": next_id,
                    "run_id": str(run_id),
                    "trend_idx": idx,
                    "title": t.get("title"),
                    "description": t.get("description"),
                    "kind": t.get("kind"),
                    "horizon": t.get("horizon"),
                    "strength": t.get("strength"),
                    "confidence": t.get("confidence"),
                    "evidence_json": _json(t.get("evidence") or []),
                    "industry_groups_json": _json(t.get("industry_groups") or []),
                    "loser_note": t.get("loser_note"),
                },
            )
            id_by_idx[idx] = next_id
            next_id += 1
        n_links = 0
        link_id = _next_id(conn, _TREND_LINKS)
        for ln in links:
            trend_id = id_by_idx.get(int(ln.get("trend_idx", -1)))
            if trend_id is None or not ln.get("uid"):
                continue
            conn.execute(
                text(
                    f"INSERT INTO {_TREND_LINKS} (id, run_id, trend_id, uid, ticker, role, "
                    "pricing_llm, pricing_quant, action, reasoning_json, evidence_json) VALUES "
                    "(:id, :run_id, :trend_id, :uid, :ticker, :role, :pricing_llm, "
                    ":pricing_quant, :action, :reasoning_json, :evidence_json)"
                ),
                {
                    "id": link_id,
                    "run_id": str(run_id),
                    "trend_id": trend_id,
                    "uid": str(ln["uid"]),
                    "ticker": ln.get("ticker"),
                    "role": ln.get("role"),
                    "pricing_llm": ln.get("pricing_llm"),
                    "pricing_quant": ln.get("pricing_quant"),
                    "action": ln.get("action"),
                    "reasoning_json": _json(ln.get("reasoning") or []),
                    "evidence_json": _json(ln.get("evidence") or []),
                },
            )
            link_id += 1
            n_links += 1
    return len(trends), n_links


def _decode_link(row: dict) -> dict:
    row["reasoning"] = _loads(row.pop("reasoning_json", None)) or []
    row["evidence"] = _loads(row.pop("evidence_json", None)) or []
    row["created_at"] = _ts(row.get("created_at"))
    return row


def list_trends(run_id: str | None = None, limit: int = 20) -> list[dict]:
    """Trends eines Laufs inkl. ``links``; ohne ``run_id`` der neueste Lauf."""
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_trends(conn)
            rid = run_id or _latest_run_with_rows(conn, _TRENDS)
            if not rid:
                return []
            trends = _rows(
                conn,
                f"SELECT * FROM {_TRENDS} WHERE run_id = :r ORDER BY trend_idx LIMIT :n",
                {"r": str(rid), "n": int(limit)},
            )
            links = _rows(
                conn,
                f"SELECT * FROM {_TREND_LINKS} WHERE run_id = :r ORDER BY id",
                {"r": str(rid)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Trends fehlgeschlagen: %s", exc)
        return []
    by_trend: dict[int, list[dict]] = {}
    for ln in links:
        by_trend.setdefault(int(ln["trend_id"]), []).append(_decode_link(ln))
    out = []
    for t in trends:
        t["evidence"] = _loads(t.pop("evidence_json", None)) or []
        t["industry_groups"] = _loads(t.pop("industry_groups_json", None)) or []
        t["created_at"] = _ts(t.get("created_at"))
        t["links"] = by_trend.get(int(t["id"]), [])
        out.append(t)
    return out


def load_latest_trend_links() -> pd.DataFrame:
    """Profiteur-Links des neuesten Laufs mit Trends (``uid, ticker,
    trend_title, role, pricing_quant, pricing_llm, action, reasoning``)."""
    cols = ["uid", "ticker", "trend_title", "role", "pricing_quant", "pricing_llm", "action", "reasoning"]
    empty = pd.DataFrame(columns=cols)
    trends = list_trends()
    if not trends:
        return empty
    rows = []
    for t in trends:
        for ln in t["links"]:
            rows.append(
                {
                    "uid": ln["uid"],
                    "ticker": ln.get("ticker"),
                    "trend_title": t.get("title"),
                    "role": ln.get("role"),
                    "pricing_quant": ln.get("pricing_quant"),
                    "pricing_llm": ln.get("pricing_llm"),
                    "action": ln.get("action"),
                    "reasoning": ln.get("reasoning") or [],
                }
            )
    return pd.DataFrame(rows, columns=cols) if rows else empty


def load_trend_links_for(uid: str, limit: int = 10) -> list[dict]:
    """Trend-Links eines Titels über alle Läufe (neueste zuerst), mit
    Trend-Titel."""
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_trends(conn)
            rows = _rows(
                conn,
                f"SELECT l.*, t.title AS trend_title, t.kind AS trend_kind, "
                f"t.horizon AS trend_horizon FROM {_TREND_LINKS} l "
                f"JOIN {_TRENDS} t ON t.id = l.trend_id "
                "WHERE l.uid = :u ORDER BY l.id DESC LIMIT :n",
                {"u": str(uid), "n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Trend-Links fehlgeschlagen: %s", exc)
        return []
    return [_decode_link(r) for r in rows]


# --------------------------------------------------------------------------- #
# Watch-State (Cursor je Titel)
# --------------------------------------------------------------------------- #
_WATCH_COLS = (
    "av_symbol", "last_earnings_report_date", "last_transcript_quarter",
    "last_insider_date", "last_news_at", "last_zone", "last_score",
    "last_z_quality", "last_checked_at",
)


def _decode_watch(row: dict) -> dict:
    row["last_earnings_report_date"] = _d(row.get("last_earnings_report_date"))
    row["last_insider_date"] = _d(row.get("last_insider_date"))
    row["last_news_at"] = _ts(row.get("last_news_at"))
    row["last_checked_at"] = _ts(row.get("last_checked_at"))
    row["updated_at"] = _ts(row.get("updated_at"))
    return row


def load_watch_state(uid: str) -> dict | None:
    states = load_watch_states([uid])
    return states.get(str(uid))


def load_watch_states(uids: list[str]) -> dict[str, dict]:
    engine = persistence.get_engine()
    if engine is None or not uids:
        return {}
    out: dict[str, dict] = {}
    try:
        with engine.begin() as conn:
            _ensure_watch(conn)
            keys = [str(u) for u in uids]
            for i in range(0, len(keys), 200):
                chunk = keys[i : i + 200]
                placeholders = ", ".join(f":k{j}" for j in range(len(chunk)))
                rows = _rows(
                    conn,
                    f"SELECT * FROM {_WATCH} WHERE uid IN ({placeholders})",
                    {f"k{j}": k for j, k in enumerate(chunk)},
                )
                for r in rows:
                    out[str(r["uid"])] = _decode_watch(r)
    except SQLAlchemyError as exc:
        log.warning("Laden des Watch-State fehlgeschlagen: %s", exc)
        return {}
    return out


def save_watch_state(uid: str, **fields) -> None:
    """UPSERT des Cursors; nur übergebene Felder werden gesetzt, die
    übrigen bleiben erhalten."""
    cols = {k: v for k, v in fields.items() if k in _WATCH_COLS}
    with _engine().begin() as conn:
        _ensure_watch(conn)
        existing = conn.execute(
            text(f"SELECT 1 FROM {_WATCH} WHERE uid = :u"), {"u": str(uid)}
        ).fetchone()
        if existing is None:
            all_cols = {c: cols.get(c) for c in _WATCH_COLS}
            conn.execute(
                text(
                    f"INSERT INTO {_WATCH} (uid, {', '.join(_WATCH_COLS)}) "
                    f"VALUES (:uid, {', '.join(':' + c for c in _WATCH_COLS)})"
                ),
                {**all_cols, "uid": str(uid)},
            )
        elif cols:
            sets = ", ".join(f"{c} = :{c}" for c in cols)
            conn.execute(
                text(
                    f"UPDATE {_WATCH} SET {sets}, updated_at = CURRENT_TIMESTAMP "
                    "WHERE uid = :uid"
                ),
                {**cols, "uid": str(uid)},
            )


# --------------------------------------------------------------------------- #
# KI-Chat: Sitzungen und Nachrichten
# --------------------------------------------------------------------------- #
def create_chat_session(title: str | None = None, context_uid: str | None = None) -> int:
    with _engine().begin() as conn:
        _ensure_chat_sessions(conn)
        sid = _next_id(conn, _CHAT_SESSIONS)
        conn.execute(
            text(
                f"INSERT INTO {_CHAT_SESSIONS} (id, title, context_uid, created_at, updated_at, n_messages) "
                "VALUES (:id, :title, :ctx, :now, :now, 0)"
            ),
            {"id": sid, "title": title or "Neue Unterhaltung", "ctx": context_uid, "now": datetime.now()},
        )
    return sid


def _decode_session(row: dict) -> dict:
    row["created_at"] = _ts(row.get("created_at"))
    row["updated_at"] = _ts(row.get("updated_at"))
    return row


def list_chat_sessions(limit: int = 50) -> list[dict]:
    engine = persistence.get_engine()
    if engine is None:
        return []
    try:
        with engine.begin() as conn:
            _ensure_chat_sessions(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_CHAT_SESSIONS} ORDER BY updated_at DESC, id DESC LIMIT :n",
                {"n": int(limit)},
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Chat-Sitzungen fehlgeschlagen: %s", exc)
        return []
    return [_decode_session(r) for r in rows]


def load_chat_session(session_id: int) -> dict | None:
    engine = persistence.get_engine()
    if engine is None:
        return None
    try:
        with engine.begin() as conn:
            _ensure_chat_sessions(conn)
            rows = _rows(conn, f"SELECT * FROM {_CHAT_SESSIONS} WHERE id = :id", {"id": int(session_id)})
    except SQLAlchemyError as exc:
        log.warning("Laden der Chat-Sitzung fehlgeschlagen: %s", exc)
        return None
    return _decode_session(rows[0]) if rows else None


def rename_chat_session(session_id: int, title: str) -> None:
    with _engine().begin() as conn:
        _ensure_chat_sessions(conn)
        conn.execute(
            text(f"UPDATE {_CHAT_SESSIONS} SET title = :t, updated_at = :now WHERE id = :id"),
            {"t": str(title)[:120], "now": datetime.now(), "id": int(session_id)},
        )


def delete_chat_session(session_id: int) -> None:
    with _engine().begin() as conn:
        _ensure_chat_sessions(conn)
        _ensure_chat_messages(conn)
        conn.execute(text(f"DELETE FROM {_CHAT_MESSAGES} WHERE session_id = :id"), {"id": int(session_id)})
        conn.execute(text(f"DELETE FROM {_CHAT_SESSIONS} WHERE id = :id"), {"id": int(session_id)})


def touch_chat_session(session_id: int) -> None:
    with _engine().begin() as conn:
        _ensure_chat_sessions(conn)
        conn.execute(
            text(f"UPDATE {_CHAT_SESSIONS} SET updated_at = :now WHERE id = :id"),
            {"now": datetime.now(), "id": int(session_id)},
        )


def append_chat_message(
    session_id: int,
    role: str,
    content: str,
    *,
    tool_name: str | None = None,
    tool_calls=None,
    trace=None,
    model: str | None = None,
    n_llm_calls: int | None = None,
    n_av_calls: int | None = None,
) -> int:
    if role not in CHAT_ROLES:
        raise ValueError(f"Unbekannte Chat-Rolle: {role}")
    with _engine().begin() as conn:
        _ensure_chat_sessions(conn)
        _ensure_chat_messages(conn)
        mid = _next_id(conn, _CHAT_MESSAGES)
        conn.execute(
            text(
                f"INSERT INTO {_CHAT_MESSAGES} (id, session_id, role, content, tool_name, tool_calls_json, "
                "trace_json, model, n_llm_calls, n_av_calls, created_at) VALUES (:id, :sid, :role, :content, "
                ":tool_name, :tool_calls_json, :trace_json, :model, :n_llm, :n_av, :now)"
            ),
            {
                "id": mid, "sid": int(session_id), "role": role, "content": content,
                "tool_name": tool_name, "tool_calls_json": _json(tool_calls) if tool_calls is not None else None,
                "trace_json": _json(trace) if trace is not None else None, "model": model,
                "n_llm": n_llm_calls, "n_av": n_av_calls, "now": datetime.now(),
            },
        )
        conn.execute(
            text(
                f"UPDATE {_CHAT_SESSIONS} SET n_messages = n_messages + 1, updated_at = :now WHERE id = :id"
            ),
            {"now": datetime.now(), "id": int(session_id)},
        )
    return mid


def _decode_chat_message(row: dict) -> dict:
    row["tool_calls"] = _loads(row.pop("tool_calls_json", None))
    row["trace"] = _loads(row.pop("trace_json", None))
    row["created_at"] = _ts(row.get("created_at"))
    return row


def load_chat_messages(
    session_id: int, limit: int = 200, roles: tuple[str, ...] | None = None
) -> list[dict]:
    """Nachrichten einer Sitzung, aufsteigend (die letzten ``limit``)."""
    engine = persistence.get_engine()
    if engine is None:
        return []
    params: dict = {"sid": int(session_id), "n": int(limit)}
    clause = ""
    if roles:
        placeholders = ", ".join(f":r{i}" for i in range(len(roles)))
        clause = f" AND role IN ({placeholders})"
        params.update({f"r{i}": r for i, r in enumerate(roles)})
    try:
        with engine.begin() as conn:
            _ensure_chat_messages(conn)
            rows = _rows(
                conn,
                f"SELECT * FROM {_CHAT_MESSAGES} WHERE session_id = :sid{clause} ORDER BY id DESC LIMIT :n",
                params,
            )
    except SQLAlchemyError as exc:
        log.warning("Laden der Chat-Nachrichten fehlgeschlagen: %s", exc)
        return []
    return [_decode_chat_message(r) for r in reversed(rows)]
