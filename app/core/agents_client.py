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


_NEUT_SCHEME_TEXT = {
    "industry_group_then_region": (
        "z-score within the global GICS industry group, then re-standardised "
        "within the region"
    ),
    "sector_then_region": (
        "z-score within the global GICS sector, then re-standardised within "
        "the region"
    ),
    "region_only": "z-score within the region only (keeps the industry tilt)",
    "region_sector": (
        "z-score within region x sector (fallback sector, then global)"
    ),
    "global": "z-score across the whole universe",
}

_DIRECTION_TEXT = {1: "higher is better", -1: "lower is better"}


def _pct(value: float, digits: int = 0) -> str:
    return f"{float(value) * 100:.{digits}f}%"


def build_v2_methodology(
    settings, segment: str, indicator_map: dict[str, list[tuple[str, float]]]
) -> list[str]:
    """Kurzbeschreibung der Composite-v2-Systematik für die Agenten.

    Englische Sätze (Prompt-Sprache des Service), parametrisiert aus den
    Settings (Gewichte, Neutralisierung, Winsorisierung, Mindestabdeckung,
    Klassen-/Zonen-Schwellen, Universumsfilter) und dem Segment der Zeile
    (Spec 2, 3, 4, 5.2; MODEL_DESCRIPTION 11.3–11.5, 12.1).
    """
    g = lambda name, default: getattr(settings, name, default)  # noqa: E731
    weights = settings.v2_factor_weights() if settings is not None else {}
    lines: list[str] = []

    weight_txt = ", ".join(
        f"{f.capitalize()} {float(w):.2f}" for f, w in weights.items()
    )
    lines.append(
        "Composite v2 is a relative, z-score based 4-factor ranking of the "
        "multi_factor stock universe (Koyfin export as of the as_of date). "
        f"Strategic factor weights: {weight_txt}. Growth and low volatility "
        "are not factors; Piotroski acts only as a universe filter and "
        "Altman-Z is informational only (not a filter)."
    )

    seg_label = {
        "financial": "financials",
        "real_estate": "real estate",
        "nonfinancial": "non-financials",
    }.get(segment, segment)
    factor_bits = []
    for factor, entries in indicator_map.items():
        if not entries:
            continue
        inds = ", ".join(
            f"{name} ({_DIRECTION_TEXT.get(int(d), 'n/a')})" for name, d in entries
        )
        factor_bits.append(f"{factor.capitalize()}: {inds}")
    if factor_bits:
        lines.append(
            f"Indicator set for this stock's segment ({seg_label}), equally "
            "weighted within each factor: " + "; ".join(factor_bits) + "."
        )

    lower, upper = g("v2_winsor_lower", 0.03), g("v2_winsor_upper", 0.97)
    cap = g("v2_zscore_cap", 3.0)
    scheme_map = settings.neut_scheme_map() if settings is not None else {}
    used = {name for entries in indicator_map.values() for name, _ in entries}
    by_scheme: dict[str, list[str]] = {}
    for name in sorted(used):
        by_scheme.setdefault(scheme_map.get(name, "n/a"), []).append(name)
    scheme_txt = "; ".join(
        f"{', '.join(names)}: {_NEUT_SCHEME_TEXT.get(scheme, scheme)}"
        for scheme, names in by_scheme.items()
    )
    lines.append(
        "Each indicator is winsorised at the "
        f"{_pct(lower)}/{_pct(upper)} quantiles inside its peer group, "
        "standardised to a z-score ((x - mean) / std) and capped at "
        f"+/-{float(cap):g}; the sign is flipped for lower-is-better "
        "indicators, so every z reads 'positive = better than peers'. Peer "
        "groups (neutralisation) per indicator: "
        + (scheme_txt or "see neut_level per indicator")
        + f". Groups with fewer than {int(g('v2_min_group_size_l1', 20))} "
        "valid values fall back to the sector, then to the whole universe; "
        f"groups with fewer than {int(g('v2_min_group_valid', 5))} valid "
        "values get z = 0. The neut_level per indicator names the level "
        "actually used. Missing data stays missing (no imputation)."
    )

    min_valid = (
        g("v2_min_valid_financial", {})
        if segment == "financial"
        else g("v2_min_valid_nonfin", {})
    )
    min_txt = ", ".join(
        f"{f.capitalize()} {int(v)}" for f, v in (min_valid or {}).items()
    )
    lines.append(
        "Factor z = mean of the stock's valid indicator z-scores; a factor is "
        "missing when fewer indicators are valid than required ("
        + (min_txt or "see settings")
        + "). Factor coverage = share of valid indicators."
    )

    lines.append(
        "Composite raw = weight-averaged factor z over the factors available "
        f"for the stock; missing when the available weights sum to less than "
        f"{float(g('v2_min_factor_weight', 0.70)):.2f} or neither Value nor "
        "Quality is available. The raw composite is re-standardised across "
        "the whole universe (winsorised at "
        f"{_pct(g('v2_composite_winsor_lower', 0.01))}/"
        f"{_pct(g('v2_composite_winsor_upper', 0.99))}, capped at "
        f"+/-{float(cap):g}) into composite z. Composite pct is the "
        "percentile rank of composite z across the universe and the "
        "composite score is pct x 100: a score of 83 means the stock ranks "
        "above 83% of the universe. It is a cross-sectional ranking, not an "
        "absolute return or fair-value estimate. Data coverage is the "
        "factor-weighted mean of the factor coverages."
    )

    lines.append(
        "Classification (display only, from composite pct): A >= 90%, "
        "B+ >= 80%, B >= 66.7%, C >= 50%, D >= 33%, F below."
    )

    entry, exit_ = g("pc_entry_pct", 0.80), g("pc_exit_pct", 0.667)
    lines.append(
        "Zone (portfolio rule): FILTER = fails at least one universe filter "
        "(not investable); otherwise KANDIDAT = composite pct >= "
        f"{_pct(entry, 1)} (buy candidate), HALTEN = between "
        f"{_pct(exit_, 1)} and {_pct(entry, 1)} (hold buffer: a held stock "
        f"stays), VERKAUFEN = below {_pct(exit_, 1)} (sell)."
    )

    lines.append(
        "Universe filters (hard exclusions, all violations listed in "
        f"filter.reasons): market cap >= {float(g('filter_min_market_cap', 1000)):g} "
        f"M EUR; Piotroski >= {float(g('filter_min_piotroski', 5)):g}/9 "
        "(financials proportionally); 3-month average daily turnover >= "
        f"{float(g('filter_min_adv', 2.0)):g} M EUR when available; data "
        f"coverage >= {float(g('filter_min_coverage', 0.6)):g} and composite "
        f"computable; listed for >= {int(g('filter_min_listing_days', 365))} "
        "days when the IPO date is known; non-financials with debt/equity > "
        f"{float(g('filter_max_de', 3.0)):g} AND interest coverage < "
        f"{float(g('filter_min_icr', 2.0)):g}; manual exclusion overrides. "
        "Missing market cap or Piotroski data also fails the filter. Altman-Z "
        "is reported for information only and is deliberately not a filter "
        "(not applicable to technology, software and service companies)."
    )

    lines.append(
        "The trend warning (death cross: price below SMA-50 below SMA-200) "
        "and the technical signals are informational and do not change the "
        "composite or the zone."
    )
    return lines


def build_composite_v2_context(row, settings) -> dict:
    """``composite_v2``-Block des Faktor-Kontexts aus einer v2-Zeile.

    Enthält alle Eingaben des Composite v2 (Spec 2, 3, 4, 5.2): Composite-
    Score/Perzentil/Z, Klasse und Zone, Faktor-Z mit Abdeckung und Gewicht,
    Indikator-Z mit Rohwert, Richtung und Neutralisierungsebene, Segment,
    Universumsfilter, Trend-Warnung und eine Kurzbeschreibung der
    Systematik (:func:`build_v2_methodology`). Fehlt der Composite (z. B.
    unzureichende Faktorabdeckung), bleibt ``score`` weg — der Block trägt
    dann die vorhandenen Faktor-/Indikatordaten und den Filtergrund.
    """
    from .scoring_v2 import V2_FACTOR_NAMES

    block: dict = {}
    if (score := _num(row, "composite_score")) is not None:
        block["score"] = score
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

    indicator_map = _v2_indicator_map(row, segment)
    indicators: dict = {}
    for factor, entries in indicator_map.items():
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
    block["methodology"] = build_v2_methodology(settings, segment, indicator_map)
    return block


def build_factor_context(row: pd.Series | dict, settings=None) -> dict:
    """Baut den ``factor_context``-Payload aus einer ``STATE.scored``-Zeile.

    Folgt der aktiven Scoring-Version (``settings.scoring_version``): bei v2
    trägt der Payload statt der v1-Score-Felder ausschließlich den
    ``composite_v2``-Block (:func:`build_composite_v2_context`, inkl.
    Methodik); ohne Settings oder bei v1 bleibt der bisherige v1-Payload. Neutrale Daten
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

    # Bei aktivem Composite v2 nie v1-Score-Werte senden — auch ohne
    # berechenbaren Composite trägt der v2-Block Faktoren, Indikatoren und
    # den Filtergrund (die Agenten sollen das aktuelle Modell sehen).
    if settings is not None and getattr(settings, "scoring_version", "v1") == "v2":
        fc["scoring_version"] = "v2"
        fc["composite_v2"] = build_composite_v2_context(row, settings)
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
    if isinstance(comp, dict) or fc.get("scoring_version") == "v2":
        comp = comp if isinstance(comp, dict) else {}
        try:
            score = float(comp["score"]) if comp.get("score") is not None else None
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
    log.info(
        "Agenten-Lauf %s (%s): factor_context scoring_version=%s, keys=%s",
        ticker,
        agents_ticker,
        (factor_context or {}).get("scoring_version", "-"),
        sorted(factor_context) if factor_context else "-",
    )

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
