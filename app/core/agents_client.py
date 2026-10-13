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

import numpy as np
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


# Erfolgs-Cache der Symbol-Suche: Tipp-Sequenzen im Autocomplete ("merc",
# "merce", …) treffen den Service je Begriff nur einmal — Alpha Vantage ist
# rate-limitiert. Fehler werden nicht gecacht.
_SEARCH_CACHE: dict[str, tuple[float, list[dict], str | None]] = {}
_SEARCH_CACHE_LOCK = threading.Lock()
_SEARCH_CACHE_TTL = 600.0
_SEARCH_CACHE_MAX = 256


def symbol_search(query: str) -> tuple[list[dict], str | None]:
    """Symbol-Suche des Service (liefert Yahoo-Dialekt-Symbole).

    Rückgabe ``(results, note)``; ``note`` ist eine deutsche Hinweismeldung
    des Service (z. B. fehlender Alpha-Vantage-Key) oder ``None``.
    Erfolgreiche Antworten werden für kurze Zeit gecacht.
    """
    key = (query or "").strip().lower()
    now = time.time()
    with _SEARCH_CACHE_LOCK:
        cached = _SEARCH_CACHE.get(key)
        if cached and now - cached[0] < _SEARCH_CACHE_TTL:
            return list(cached[1]), cached[2]

    try:
        resp = requests.get(
            f"{base_url()}/api/symbol-search",
            params={"q": query},
            timeout=_CATALOG_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        note = data.get("note") or None
        note_text = note.get("text") if isinstance(note, dict) else note
        results = list(data.get("results") or [])
    except Exception as exc:  # noqa: BLE001
        log.info("Symbol-Suche fehlgeschlagen: %s", exc)
        return [], "TradingAgents-Service nicht erreichbar."

    with _SEARCH_CACHE_LOCK:
        if len(_SEARCH_CACHE) >= _SEARCH_CACHE_MAX:
            _SEARCH_CACHE.clear()
        _SEARCH_CACHE[key] = (now, results, note_text)
    return list(results), note_text


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


def _bool(row, key) -> bool | None:
    val = row.get(key)
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (bool, np.bool_)):
        return bool(val)
    if isinstance(val, (int, float, np.integer, np.floating)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "ja", "yes", "wahr")
    return None


def _round(val: float, digits: int = 4) -> float:
    return float(round(val, digits))


# Rohwert-Spalte je v2-Indikator, falls sie vom Indikatornamen abweicht
# (``fcf_yield`` nutzt die kombinierte Serie aus ``derive_v2_indicators``).
_V2_RAW_COLUMN = {"fcf_yield": "fcf_yield_v2"}
# Leverage-Platzhalter (``__leverage__``) → tatsächlich berechnete Spalte, in
# der Auflösungsreihenfolge von ``scoring_v2._resolve_leverage_indicator``.
_V2_LEVERAGE_CANDIDATES = ("net_debt_ebitda", "debt_ebit", "debt_equity")

# Zone (v2) → Handlungsrichtung, Pendant zur v1-``recommendation``.
ZONE_DIRECTION = {
    "KANDIDAT": "buy",
    "HALTEN": "hold",
    "VERKAUFEN": "sell",
}


def _v2_segment(row) -> str:
    if _bool(row, "is_financial"):
        return "financial"
    if _bool(row, "is_real_estate"):
        return "real_estate"
    return "nonfinancial"


def _v2_indicator_map(row, segment: str) -> dict[str, list[tuple[str, float]]]:
    """Indikatorlisten je Faktor für das Segment der Zeile (Spec 2.1–2.3).

    Spiegelt ``scoring_v2._factor_indicator_map`` auf Zeilenebene: der
    Leverage-Platzhalter wird über die vorhandene ``z_``-Spalte aufgelöst,
    ``accruals`` entfällt bei Real Estate.
    """
    from .scoring_v2 import FINANCIAL_FACTORS, NONFIN_FACTORS

    if segment == "financial":
        return {f: list(entries) for f, entries in FINANCIAL_FACTORS.items()}
    leverage = next(
        (c for c in _V2_LEVERAGE_CANDIDATES if _num(row, f"z_{c}") is not None),
        None,
    )
    out: dict[str, list[tuple[str, float]]] = {}
    for factor, entries in NONFIN_FACTORS.items():
        resolved = []
        for name, direction in entries:
            if name == "__leverage__":
                if leverage:
                    resolved.append((leverage, direction))
            elif segment == "real_estate" and name == "accruals":
                continue
            else:
                resolved.append((name, direction))
        out[factor] = resolved
    return out


def build_composite_v2_context(row, settings) -> dict | None:
    """``composite_v2``-Block des Faktor-Kontexts aus einer v2-Zeile.

    Enthält alle Eingaben des Composite v2 (Spec 2, 3, 4, 5.2): Composite-
    Score/Perzentil/Z, Klasse und Zone, Faktor-Z mit Abdeckung und Gewicht,
    Indikator-Z mit Rohwert, Richtung und Neutralisierungsebene, Segment,
    Universumsfilter und Trend-Warnung. ``None``, wenn die Zeile keinen
    gültigen Composite trägt.
    """
    from .scoring_v2 import V2_FACTOR_NAMES

    score = _num(row, "composite_score")
    if score is None:
        return None

    block: dict = {"score": score}
    for key, name in (
        ("composite_pct", "pct"),
        ("composite_z", "z"),
        ("composite_raw", "raw"),
        ("data_coverage_v2", "data_coverage"),
    ):
        if (val := _num(row, key)) is not None:
            block[name] = _round(val)
    for key, name in (("classification_v2", "classification"), ("zone_v2", "zone")):
        if (val := _txt(row, key)) is not None:
            block[name] = val
    if settings is not None:
        block["zone_thresholds"] = {
            "entry_pct": float(settings.pc_entry_pct),
            "exit_pct": float(settings.pc_exit_pct),
        }

    segment = _v2_segment(row)
    block["segment"] = segment

    weights = settings.v2_factor_weights() if settings is not None else {}
    factors: dict = {}
    for factor in V2_FACTOR_NAMES:
        entry: dict = {}
        if (z := _num(row, f"z_{factor}")) is not None:
            entry["z"] = _round(z)
        if (cov := _num(row, f"cov_{factor}")) is not None:
            entry["coverage"] = _round(cov)
        if factor in weights:
            entry["weight_strategic"] = _round(float(weights[factor]))
        if entry:
            factors[factor] = entry
    if factors:
        block["factors"] = factors

    indicators: dict = {}
    for factor, entries in _v2_indicator_map(row, segment).items():
        for name, direction in entries:
            z = _num(row, f"z_{name}")
            raw = _num(row, _V2_RAW_COLUMN.get(name, name))
            if z is None and raw is None:
                continue
            ind: dict = {"factor": factor, "direction": int(direction)}
            if z is not None:
                ind["z"] = _round(z)
            if raw is not None:
                ind["value"] = _round(raw)
            if (level := _txt(row, f"neut_level_{name}")) is not None:
                ind["neut_level"] = level
            if name == "fcf_yield" and (src := _txt(row, "fcf_yield_source")):
                ind["source"] = src
            indicators[name] = ind
    if indicators:
        block["indicators"] = indicators

    filt: dict = {}
    if (passed := _bool(row, "filter_pass")) is not None:
        filt["pass"] = passed
    reasons = row.get("filter_reasons")
    if isinstance(reasons, (list, tuple, np.ndarray)):
        filt["reasons"] = [str(r) for r in reasons if str(r)]
    if filt:
        block["filter"] = filt

    if (warn := _bool(row, "trend_warning")) is not None:
        block["trend_warning"] = warn
    return block


def build_factor_context(row: pd.Series | dict, settings=None) -> dict:
    """Baut den ``factor_context``-Payload aus einer ``STATE.scored``-Zeile.

    Folgt der aktiven Scoring-Version (``settings.scoring_version``): bei v2
    trägt der Payload statt der v1-Score-Felder den vollständigen
    ``composite_v2``-Block (:func:`build_composite_v2_context`); ohne
    Settings oder bei v1 bleibt der bisherige v1-Payload. Neutrale Daten
    (Piotroski, Altman-Z, SMA-Signale, Identität) werden immer mitgegeben.

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

    composite = None
    if settings is not None and getattr(settings, "scoring_version", "v1") == "v2":
        composite = build_composite_v2_context(row, settings)

    if composite is not None:
        fc["scoring_version"] = "v2"
        fc["composite_v2"] = composite
    else:
        fc["scoring_version"] = "v1"
        if (val := _num(row, "total_score")) is not None:
            fc["total_score"] = val
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

    for key in ("piotroski", "altman_z"):
        if (val := _num(row, key)) is not None:
            fc[key] = val

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


def primary_quant(factor_context: dict | None) -> tuple[float | None, str | None, str]:
    """``(score, klassifikation, version)`` des Primär-Scorings eines Kontexts.

    v2-Kontexte (``composite_v2``) liefern Composite-Score und Klasse v2,
    v1-Kontexte ``total_score``/``classification``. Version ``"v1"`` auch
    für leere Kontexte, damit Aufrufer nicht auf ``None`` prüfen müssen.
    """
    fc = factor_context or {}
    comp = fc.get("composite_v2")
    if isinstance(comp, dict) and comp.get("score") is not None:
        try:
            score = float(comp["score"])
        except (TypeError, ValueError):
            score = None
        cls = comp.get("classification")
        return score, (str(cls) if cls else None), "v2"
    score = fc.get("total_score")
    try:
        score = float(score) if score is not None else None
    except (TypeError, ValueError):
        score = None
    cls = fc.get("classification")
    return score, (str(cls) if cls else None), "v1"


def quant_direction(factor_context: dict | None) -> str | None:
    """Handlungsrichtung (``buy``/``hold``/``sell``) des Quant-Kontexts.

    v2: aus der Zone (FILTER → keine Richtung); v1: aus ``recommendation``.
    """
    fc = factor_context or {}
    comp = fc.get("composite_v2")
    if isinstance(comp, dict):
        return ZONE_DIRECTION.get(str(comp.get("zone") or "").upper())
    rec = str(fc.get("recommendation") or "").upper()
    if rec in ("BUY", "STRONG BUY"):
        return "buy"
    if rec == "SELL":
        return "sell"
    if rec == "HOLD":
        return "hold"
    return None


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
    # Env-Override des Service (TRADINGAGENTS_LLM_BACKEND_URL) respektieren:
    # ohne backend_url im Payload fiele build_run() serverseitig auf die
    # Provider-Standard-URL zurück (die Browser-UI sendet den Wert genauso mit).
    backend_url = (defaults.get("backend_url") or "").strip()
    if backend_url:
        payload["backend_url"] = backend_url
    # Sprache der Agenten-Reports (Service-Default ist English; die App
    # sendet standardmäßig German — einstellbar unter „Einstellungen“).
    language = (getattr(settings, "agents_language", "") or "").strip()
    if language:
        payload["output_language"] = language
    # Temperatur: App-Default 0 (deterministisch); None hieße Service-Default.
    temperature = getattr(settings, "agents_temperature", 0.0)
    if temperature is not None:
        try:
            payload["temperature"] = max(0.0, min(2.0, float(temperature)))
        except (TypeError, ValueError):
            pass
    # Vergleich mit letzter Analyse: immer explizit senden, damit der
    # UI-Schalter maßgeblich ist (fehlender Key hieße Service-Default).
    payload["previous_analysis_enabled"] = bool(
        getattr(settings, "agents_prev_analysis", True)
    )
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


def current_agent(job: dict) -> str | None:
    """Name des gerade arbeitenden Agenten eines Jobs (oder Stufen-Text)."""
    states = job.get("agent_states") or {}
    for name, status in states.items():
        if status == "in_progress":
            return name
    stage = job.get("stage")
    return str(stage) if stage else None


def list_jobs() -> dict[str, dict]:
    """Snapshot aller Jobs dieser Prozess-Session (Ticker → Job-Dict).

    Grundlage der globalen Status-Übersicht auf der Agenten-Analyse-Seite:
    zeigt laufende, fehlgeschlagene und abgeschlossene Läufe unabhängig
    davon, von welcher Seite sie gestartet wurden. Neueste zuerst.
    """
    with _JOBS_LOCK:
        jobs = {t: dict(j) for t, j in _JOBS.items()}
    return dict(
        sorted(jobs.items(), key=lambda kv: kv[1].get("started_at") or 0, reverse=True)
    )


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
    # Primär-Score der Scoring-Version des Kontexts (v2: Composite, v1: total).
    primary_score, primary_class, _ = primary_quant(factor_context)
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
        "total_score": primary_score,
        "classification": primary_class,
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


# Signaturen transienter Fehler des LLM-Anbieters (5xx/Überlast/Timeout) —
# z. B. Ollama Clouds "InternalServerError: Error code: 500 - {'error':
# 'Internal Server Error (ref: …)'}". Solche Läufe scheitern nicht an der
# Eingabe, ein Neustart genügt meist.
_TRANSIENT_MARKERS = (
    "internalservererror",
    "error code: 5",
    "overloaded",
    "service unavailable",
    "timed out",
    "timeout",
)


def _friendly_error(msg: str) -> str:
    """Ergänzt bei transienten Provider-Fehlern einen Neustart-Hinweis."""
    text = str(msg or "Unbekannter Fehler")
    if any(marker in text.lower() for marker in _TRANSIENT_MARKERS):
        return (
            f"{text[:300]} — vorübergehender Fehler beim LLM-Anbieter; "
            "die Analyse kann einfach neu gestartet werden."
        )
    return text


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
                _set_job(
                    ticker,
                    status="error",
                    error=_friendly_error(str(detail)[:500]),
                )
                return

            for event, data in _iter_sse(resp):
                if event == "run":
                    # Der Service liefert agents als Liste von Dicts
                    # ({team, name, status}); die Registry und alle Render-
                    # Helfer arbeiten mit Namens-Strings (agent_states ist
                    # ebenfalls nach Name verschlüsselt). Namen extrahieren.
                    raw = data.get("agents") or []
                    names = [a.get("name") if isinstance(a, dict) else a for a in raw]
                    _set_job(ticker, agents=[n for n in names if n])
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
                        error=_friendly_error(data.get("message")),
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
