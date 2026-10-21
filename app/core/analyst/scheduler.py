"""In-App-Scheduler des KI-Analysten (Daemon-Thread).

Die App läuft single-process (siehe ``main.py``), daher genügt ein
Lock-geschütztes Status-Dict wie bei ``agents_client``. Die Schleife
wartet ``analyst_interval_minutes`` auf ein Wake-Event; läuft ab, wenn
``analyst_enabled`` gesetzt ist, oder sofort nach :func:`trigger_now`.
Fehler eines Laufs landen in ``last_error`` — der Thread stirbt nie.

``KI_ANALYST_SCHEDULER=0`` unterdrückt den Start (Tests, CLI-only-Betrieb).
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta

from ..state import STATE

log = logging.getLogger(__name__)

_MIN_INTERVAL_SECONDS = 60

_LOCK = threading.Lock()
_STATUS: dict = {
    "status": "idle",
    "last_run_id": None,
    "last_started_at": None,
    "last_finished_at": None,
    "last_status": None,
    "last_error": None,
    "next_run_at": None,
    "progress": None,
    "thread_alive": False,
}
_WAKE = threading.Event()
_STOP = threading.Event()
_MANUAL = threading.Event()
_RUN_LOCK = threading.Lock()
_thread: threading.Thread | None = None


def _set(**fields) -> None:
    with _LOCK:
        _STATUS.update(fields)


def get_status() -> dict:
    with _LOCK:
        out = dict(_STATUS)
    out["thread_alive"] = bool(_thread is not None and _thread.is_alive())
    out["running"] = _RUN_LOCK.locked()
    return out


def is_running() -> bool:
    return _RUN_LOCK.locked()


def _interval_seconds() -> int:
    try:
        minutes = int(getattr(STATE.settings, "analyst_interval_minutes", 360))
    except (TypeError, ValueError):
        minutes = 360
    return max(_MIN_INTERVAL_SECONDS, minutes * 60)


def _enabled() -> bool:
    return bool(getattr(STATE.settings, "analyst_enabled", False))


def _progress(msg: str) -> None:
    _set(progress=msg)


def _execute(trigger: str) -> None:
    """Ein Lauf unter ``_RUN_LOCK``; Exceptions nur protokollieren."""
    if not _RUN_LOCK.acquire(blocking=False):
        return
    try:
        from .runner import AnalystConfigError, run_analyst

        _set(status="running", last_started_at=datetime.now(), last_error=None, progress="Start …")
        try:
            result = run_analyst(STATE.settings, STATE.scored, STATE, trigger=trigger, progress=_progress)
            _set(last_run_id=result.run_id, last_status=result.status, last_error=result.error)
        except AnalystConfigError as exc:
            _set(last_error=str(exc), last_status="error")
        except Exception as exc:  # noqa: BLE001 — Thread darf nie sterben
            log.exception("KI-Analyst-Lauf fehlgeschlagen")
            _set(last_error=f"{exc.__class__.__name__}: {exc}", last_status="error")
        finally:
            _set(status="idle", last_finished_at=datetime.now(), progress=None)
    finally:
        _RUN_LOCK.release()


def _loop() -> None:
    while not _STOP.is_set():
        interval = _interval_seconds()
        enabled = _enabled()
        _set(next_run_at=(datetime.now() + timedelta(seconds=interval)) if enabled else None)
        woke = _WAKE.wait(interval)
        _WAKE.clear()
        if _STOP.is_set():
            break
        if _MANUAL.is_set():
            _MANUAL.clear()
            _execute("manual")
            continue
        if woke:
            # Einstellungen geändert → Intervall neu berechnen.
            continue
        if _enabled():
            _execute("scheduler")
    _set(next_run_at=None)


def start() -> bool:
    """Startet den Thread (idempotent). ``False``, wenn unterdrückt."""
    global _thread
    if os.getenv("KI_ANALYST_SCHEDULER", "1").strip() == "0":
        return False
    if _thread is not None and _thread.is_alive():
        return True
    _STOP.clear()
    _WAKE.clear()
    _MANUAL.clear()
    _thread = threading.Thread(target=_loop, name="ki-analyst-scheduler", daemon=True)
    _thread.start()
    _set(thread_alive=True)
    return True


def stop(timeout: float = 5.0) -> None:
    global _thread
    _STOP.set()
    _WAKE.set()
    if _thread is not None:
        _thread.join(timeout)
    _thread = None
    _set(thread_alive=False, next_run_at=None)


def trigger_now() -> tuple[bool, str]:
    """Manuellen Lauf anstoßen; Rückgabe ``(ok, Meldung)``."""
    from .. import av_client
    from . import ollama_client

    if _RUN_LOCK.locked():
        return False, "Ein Lauf ist bereits aktiv."
    if STATE.scored is None or STATE.scored.empty:
        return False, "Kein Universum geladen."
    if not av_client.api_key():
        return False, "ALPHAVANTAGE_API_KEY ist nicht gesetzt."
    if not ollama_client.api_key():
        return False, "OLLAMA_API_KEY ist nicht gesetzt."
    if not start():
        return False, "Scheduler ist deaktiviert (KI_ANALYST_SCHEDULER=0)."
    _MANUAL.set()
    _WAKE.set()
    return True, "Lauf gestartet."


def notify_settings_changed() -> None:
    """Weckt die Schleife, damit Intervall/Aktivierung neu gelesen werden."""
    _WAKE.set()
