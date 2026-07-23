"""HTTP-Client für den TradingAgents-Analyse-Service.

Startet Tiefenanalyse-Läufe über ``POST /api/run`` (SSE-Stream) auf einem
daemon-Hintergrund-Thread und hält den Fortschritt in einer Lock-geschützten
Job-Registry, die die Dash-UI per ``dcc.Interval`` pollt. Abgeschlossene
Läufe werden über :mod:`app.core.persistence` dauerhaft gespeichert; bei
einem Verbindungsabbruch dient das Run-Archiv des Service
(``GET /api/reports``) als durable Rückfallebene.

Die App läuft single-process (siehe ``main.py``), daher ist die In-Memory-
Registry sicher — gleiche Idiomatik wie das ``STATE``-Singleton.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import date, datetime

import pandas as pd
import requests

from . import persistence

log = logging.getLogger(__name__)

# (connect, read) — der Read-Timeout muss lange Agenten-Läufe überleben;
# zwischen SSE-Events vergehen bei Deep-Thinking-Modellen etliche Minuten.
_TIMEOUT = (10, 1800)
_CATALOG_TIMEOUT = (5, 15)

_JOBS: dict[str, dict] = {}
_JOBS_LOCK = threading.Lock()

_catalog_cache: dict | None = None
_catalog_failed_at: float | None = None
_catalog_lock = threading.Lock()
_CATALOG_RETRY_SECONDS = 30.0


def base_url() -> str:
    return os.getenv("TRADINGAGENTS_URL", "http://localhost:8000").rstrip("/")


# --------------------------------------------------------------------------- #
# Katalog & Symbol-Suche (dünne, fail-open Proxys)
# --------------------------------------------------------------------------- #
def get_catalog(force: bool = False) -> dict | None:
    """Provider-/Modell-/Tiefen-Katalog des Service (gecacht) oder ``None``.

    Fehlversuche werden für kurze Zeit negativ gecacht, damit ein nicht
    erreichbarer Service das Rendern der Seiten nicht bei jedem Callback
    um den Verbindungs-Timeout verzögert.
    """
    global _catalog_cache, _catalog_failed_at
    with _catalog_lock:
        if _catalog_cache is not None and not force:
            return _catalog_cache
        if (
            not force
            and _catalog_failed_at is not None
            and time.time() - _catalog_failed_at < _CATALOG_RETRY_SECONDS
        ):
            return None
        try:
            resp = requests.get(
                f"{base_url()}/api/catalog", timeout=_CATALOG_TIMEOUT
            )
            resp.raise_for_status()
            _catalog_cache = resp.json()
            _catalog_failed_at = None
        except Exception as exc:  # noqa: BLE001 — Service down ist erwartbar
            log.info("TradingAgents-Katalog nicht erreichbar: %s", exc)
            _catalog_failed_at = time.time()
            return None
        return _catalog_cache


def service_available() -> bool:
    return get_catalog() is not None


def get_models(provider: str) -> dict | None:
    """Modell-Optionen (``{"quick": [...], "deep": [...]}``) je Provider."""
    if not provider:
        return None
    try:
        resp = requests.get(
            f"{base_url()}/api/models",
            params={"provider": provider},
            timeout=_CATALOG_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.info("Modell-Katalog nicht erreichbar: %s", exc)
        return None


def symbol_search(query: str) -> tuple[list[dict], str | None]:
    """Symbol-Suche des Service (liefert Yahoo-Dialekt-Symbole).

    Rückgabe ``(results, note)``; ``note`` ist eine deutsche Hinweismeldung
    des Service (z. B. fehlender Alpha-Vantage-Key) oder ``None``.
    """
    try:
        resp = requests.get(
            f"{base_url()}/api/symbol-search",
            params={"q": query},
            timeout=_CATALOG_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        note = data.get("note") or None
        return list(data.get("results") or []), (
            note.get("text") if isinstance(note, dict) else note
        )
    except Exception as exc:  # noqa: BLE001
        log.info("Symbol-Suche fehlgeschlagen: %s", exc)
        return [], "TradingAgents-Service nicht erreichbar."


# --------------------------------------------------------------------------- #
# Faktor-Kontext aus einer Scored-Zeile
# --------------------------------------------------------------------------- #
def _num(row, key):
    val = row.get(key)
    if val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def _txt(row, key):
    val = row.get(key)
    return val if isinstance(val, str) and val.strip() else None


def build_factor_context(row: pd.Series | dict) -> dict:
    """Baut den ``factor_context``-Payload aus einer ``STATE.scored``-Zeile.

    NaN-/None-Felder werden weggelassen (Vertragskonvention mit dem
    TradingAgents-Service); das Ergebnis ist direkt JSON-serialisierbar.
    """
    if isinstance(row, pd.Series):
        row = row.to_dict()

    fc: dict = {"source": "multi_factor"}

    export_date = row.get("export_date")
    if isinstance(export_date, (datetime, date)):
        fc["as_of"] = export_date.strftime("%Y-%m-%d")
    elif isinstance(export_date, str) and export_date:
        fc["as_of"] = export_date

    for key in ("total_score", "piotroski", "altman_z"):
        if (val := _num(row, key)) is not None:
            fc[key] = val
    for key in ("classification", "filter_ok", "recommendation"):
        if (val := _txt(row, key)) is not None:
            fc[key] = val

    scores = {
        name: val
        for name, col in (
            ("value", "value_score"),
            ("quality", "quality_score"),
            ("growth", "growth_score"),
            ("momentum", "momentum_score"),
            ("lowvol", "lowvol_score"),
        )
        if (val := _num(row, col)) is not None
    }
    if scores:
        fc["factor_scores"] = scores

    signals: dict = {}
    for key in ("sma_signal", "trend_phase"):
        if (val := _txt(row, key)) is not None:
            signals[key] = val
    for key in ("mom_12_1", "dist_52w_high"):
        if (val := _num(row, key)) is not None:
            signals[key] = round(val, 4)
    if signals:
        fc["signals"] = signals

    identity = {
        key: val
        for key in ("name", "sector", "industry", "region")
        if (val := _txt(row, key)) is not None
    }
    if (mcap := _num(row, "market_cap")) is not None:
        identity["market_cap"] = mcap
    if identity:
        fc["identity"] = identity

    if (ticker := _txt(row, "ticker")) is not None:
        fc["source_ticker"] = ticker

    return fc


# --------------------------------------------------------------------------- #
# Run-Payload
# --------------------------------------------------------------------------- #
def build_run_payload(
    agents_ticker: str,
    factor_context: dict | None,
    settings,
) -> tuple[dict | None, str | None]:
    """Baut den ``POST /api/run``-Body. Rückgabe ``(payload, fehler)``.

    Provider/Modelle kommen aus den App-Einstellungen; leere Werte fallen
    auf die ``defaults`` des Service-Katalogs zurück.
    """
    catalog = get_catalog()
    if catalog is None:
        return None, "TradingAgents-Service nicht erreichbar (TRADINGAGENTS_URL prüfen)."
    defaults = catalog.get("defaults") or {}

    provider = getattr(settings, "agents_provider", "") or defaults.get("llm_provider")
    quick = getattr(settings, "agents_quick_model", "") or defaults.get("quick_think_llm")
    deep = getattr(settings, "agents_deep_model", "") or defaults.get("deep_think_llm")
    if not provider or not quick or not deep:
        return None, (
            "Kein LLM-Provider/Modell konfiguriert. Bitte in den Einstellungen "
            "unter „Agenten-Tiefenanalyse“ auswählen."
        )

    payload = {
        "ticker": agents_ticker,
        "analysis_date": date.today().strftime("%Y-%m-%d"),
        "llm_provider": provider,
        "shallow_thinker": quick,
        "deep_thinker": deep,
        "research_depth": int(getattr(settings, "agents_depth", 1) or 1),
    }
    if factor_context:
        payload["factor_context"] = factor_context
    return payload, None


# --------------------------------------------------------------------------- #
# Job-Registry
# --------------------------------------------------------------------------- #
def get_status(ticker: str) -> dict | None:
    with _JOBS_LOCK:
        job = _JOBS.get(ticker)
        return dict(job) if job else None


def _set_job(ticker: str, **updates) -> None:
    with _JOBS_LOCK:
        _JOBS.setdefault(ticker, {}).update(updates)


def _iter_sse(response):
    """Minimaler SSE-Parser: liefert ``(event, data_dict)``-Paare."""
    event, data_lines = "message", []
    for raw in response.iter_lines(decode_unicode=True):
        if raw is None:
            continue
        line = raw.strip("\r")
        if line == "":
            if data_lines:
                try:
                    yield event, json.loads("".join(data_lines))
                except json.JSONDecodeError:
                    pass
            event, data_lines = "message", []
            continue
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())


def start_analysis(
    ticker: str,
    agents_ticker: str,
    settings,
    factor_context: dict | None = None,
    in_universe: bool = True,
) -> tuple[bool, str]:
    """Startet eine Tiefenanalyse im Hintergrund. Rückgabe ``(ok, meldung)``.

    Kosten-Guardrails: pro Ticker maximal ein laufender Job, global maximal
    ein laufender Agenten-Lauf.
    """
    with _JOBS_LOCK:
        running = [t for t, j in _JOBS.items() if j.get("status") == "running"]
        if ticker in running:
            return False, "Für diesen Titel läuft bereits eine Analyse."
        if running:
            return False, (
                f"Es läuft bereits eine Analyse ({running[0]}). Bitte warten, "
                "bis sie abgeschlossen ist."
            )

    payload, err = build_run_payload(agents_ticker, factor_context, settings)
    if err:
        return False, err

    _set_job(
        ticker,
        status="running",
        stage="Starte Analyse …",
        agents=[],
        run_id=None,
        error=None,
        started_at=time.time(),
        agents_ticker=agents_ticker,
    )

    thread = threading.Thread(
        target=_run_job,
        args=(ticker, agents_ticker, payload, factor_context, in_universe),
        daemon=True,
        name=f"agents-run-{ticker}",
    )
    thread.start()
    return True, "Tiefenanalyse gestartet."


def _persist_result(
    ticker: str,
    agents_ticker: str,
    payload: dict,
    factor_context: dict | None,
    in_universe: bool,
    run_id: str | None,
    decision: str | None,
    reports: dict | None,
) -> None:
    reports = reports or {}
    record = {
        "ticker": ticker,
        "run_id": run_id or f"local_{int(time.time())}",
        "agents_ticker": agents_ticker,
        "in_universe": in_universe,
        "analysis_date": payload.get("analysis_date"),
        "rating": decision,
        "executive_summary": reports.get("final_trade_decision"),
        "reports": reports,
        "factor_context": factor_context,
        "provider": payload.get("llm_provider"),
        "total_score": (factor_context or {}).get("total_score"),
        "classification": (factor_context or {}).get("classification"),
    }
    try:
        persistence.save_agent_analysis(record)
    except Exception as exc:  # noqa: BLE001 — Anzeige klappt trotzdem (Registry)
        log.warning("Agenten-Analyse konnte nicht gespeichert werden: %s", exc)


def _try_archive_fallback(
    ticker: str, agents_ticker: str, started_at: float
) -> dict | None:
    """Sucht nach einem abgeschlossenen Run im Service-Archiv.

    Rückfallebene für abgerissene SSE-Verbindungen: das Archiv ist die
    durable Quelle der Wahrheit. Liefert das ``run.json``-Dict oder ``None``.
    """
    try:
        resp = requests.get(f"{base_url()}/api/reports", timeout=_CATALOG_TIMEOUT)
        resp.raise_for_status()
        runs = resp.json().get("reports") or resp.json().get("runs") or []
    except Exception:  # noqa: BLE001
        return None

    started = datetime.fromtimestamp(started_at).astimezone()
    for entry in runs:
        if str(entry.get("ticker", "")).upper() != agents_ticker.upper():
            continue
        created = entry.get("created_at")
        try:
            if created and datetime.fromisoformat(created) < started:
                continue
        except ValueError:
            continue
        run_id = entry.get("id")
        try:
            detail = requests.get(
                f"{base_url()}/api/reports/{run_id}", timeout=_CATALOG_TIMEOUT
            )
            detail.raise_for_status()
            return detail.json()
        except Exception:  # noqa: BLE001
            return None
    return None


def _run_job(
    ticker: str,
    agents_ticker: str,
    payload: dict,
    factor_context: dict | None,
    in_universe: bool,
) -> None:
    started_at = time.time()
    try:
        with requests.post(
            f"{base_url()}/api/run", json=payload, stream=True, timeout=_TIMEOUT
        ) as resp:
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail") or resp.text
                except Exception:  # noqa: BLE001
                    detail = resp.text
                _set_job(ticker, status="error", error=str(detail)[:500])
                return

            for event, data in _iter_sse(resp):
                if event == "run":
                    _set_job(ticker, agents=list(data.get("agents") or []))
                elif event == "status":
                    agent = data.get("agent")
                    status = data.get("status")
                    if agent:
                        _set_job(ticker, stage=f"{agent} ({status})")
                        with _JOBS_LOCK:
                            job = _JOBS.get(ticker) or {}
                            agent_states = dict(job.get("agent_states") or {})
                            agent_states[agent] = status
                            job["agent_states"] = agent_states
                elif event == "error":
                    _set_job(
                        ticker,
                        status="error",
                        error=str(data.get("message") or "Unbekannter Fehler"),
                    )
                    return
                elif event == "final":
                    _persist_result(
                        ticker,
                        agents_ticker,
                        payload,
                        factor_context,
                        in_universe,
                        data.get("run_id"),
                        data.get("decision"),
                        data.get("reports"),
                    )
                    _set_job(
                        ticker,
                        status="done",
                        run_id=data.get("run_id"),
                        stage="Abgeschlossen",
                    )
                    return

        # Stream endete ohne final-Event → Archiv befragen.
        raise ConnectionError("SSE-Stream ohne Endergebnis beendet")

    except Exception as exc:  # noqa: BLE001 — Netzwerkfehler → Archiv-Fallback
        log.info("Agenten-Lauf für %s unterbrochen (%s), prüfe Archiv …", ticker, exc)
        sidecar = _try_archive_fallback(ticker, agents_ticker, started_at)
        if sidecar:
            _persist_result(
                ticker,
                agents_ticker,
                payload,
                factor_context,
                in_universe,
                sidecar.get("id"),
                sidecar.get("decision"),
                sidecar.get("reports"),
            )
            _set_job(
                ticker, status="done", run_id=sidecar.get("id"), stage="Abgeschlossen"
            )
            return
        _set_job(
            ticker,
            status="error",
            error=(
                "Verbindung zum TradingAgents-Service abgebrochen und kein "
                f"Ergebnis im Archiv gefunden ({exc})."
            ),
        )
