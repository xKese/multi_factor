"""PDF-Export der Einzelanalyse im Editorial-Stil.

Rendert für einen Ticker eine einseitige A4-Hochformat-PDF aus dem aktuellen
``STATE.scored``-DataFrame. Bricht den Vorgang in drei Schritte:

1. Daten zusammenstellen (``build_context``) — inkl. Auto-Thesis, Sektor-/
   Industrie-Rang und Indikator-Perzentilen.
2. Jinja2-Template ``app/assets/factsheet/editorial.html.j2`` füllen.
3. WeasyPrint via Subprozess aufrufen, damit die Pango/Cairo-Bibliotheken
   aus dem Replit/Nix-Store gefunden werden, ohne den Hauptprozess der
   Dash-App zu kompromittieren.
"""

from __future__ import annotations

import atexit
import glob
import os
import struct
import subprocess
import sys
import threading
from contextlib import suppress
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.config import Settings
from app.core.indicators import INDICATOR_GROUPS, IndicatorGroup
from app.core.scoring import _clean_series
from app.core.peers import compute_peers
from app.ui.formatters import (
    fmt_de,
    fmt_market_cap,
    fmt_percent,
    fmt_signed_percent,
)


# Bewusst ausserhalb von ``app/assets/`` — Dash crawlt sein assets-Verzeichnis
# automatisch und würde sonst das PDF-CSS (inkl. @page / body { width: ... })
# in jede Seite der App injizieren.
_TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "factsheet_template"
_FONTS_DIR = _TEMPLATE_DIR / "fonts"

_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "htm", "j2"]),
    trim_blocks=False,
    lstrip_blocks=False,
)


# ---------------------------------------------------------------------------
# Helpers


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _classify_accent(classification: str | None) -> str:
    """Akzentfarbe für die Klassifikations-Pille (analog Editorial-Design)."""
    c = (classification or "").upper()
    if c.startswith("A"):
        return "#1b5e3a"
    if c.startswith("B+"):
        return "#1a3d72"
    if c.startswith("B"):
        return "#3d3a32"
    return "#737067"


def _classify_accent_v2(classification: str | None) -> str:
    """Akzentfarbe der v2-Klassen-Kurzformen ("A", "B+", …)."""
    c = (classification or "").upper()
    if c == "A":
        return "#1b5e3a"
    if c == "B+":
        return "#1a3d72"
    if c == "B":
        return "#3d3a32"
    return "#737067"


def _zone_accent(zone: str | None) -> str:
    """Print-Farbe je v2-Zone (analog UI-Tokens up/warn/down/muted)."""
    return {
        "KANDIDAT": "#1b5e3a",
        "HALTEN": "#8a6d1d",
        "VERKAUFEN": "#a8281f",
        "FILTER": "#737067",
    }.get(str(zone or ""), "#737067")


def _sma_tone(signal: str | None) -> str:
    s = str(signal or "")
    if "GOLDEN" in s.upper():
        return "up"
    if "DEATH" in s.upper():
        return "down"
    if ">" in s:
        return "up"
    if "<" in s:
        return "down"
    return "neutral"


def _recommendation_tone(rec: str | None) -> str:
    r = str(rec or "").upper()
    if "BUY" in r:
        return "up"
    if r == "SELL":
        return "down"
    return "neutral"


def _ret_tone(v: float | None) -> str:
    if v is None:
        return "na"
    return "up" if v >= 0 else "down"


# ---------------------------------------------------------------------------
# Rank, percentiles, thesis


def compute_rank(
    df: pd.DataFrame, ticker: str, score_col: str = "total_score"
) -> dict[str, int | None]:
    """Sektor- und Industrie-Rang nach ``score_col`` für einen Ticker.

    Beide Ränge werden absteigend nach Score vergeben (1 = bester). NaN-Scores
    landen am Ende. Wenn der Ticker nicht in der Gruppe ist, wird ``None``
    zurückgegeben.
    """
    out: dict[str, int | None] = {
        "sector_rank": None,
        "sector_total": None,
        "industry_rank": None,
        "industry_total": None,
    }
    if df is None or df.empty:
        return out

    from .uid import rows_by_uid_index

    idx = rows_by_uid_index(df, ticker)
    if len(idx) == 0 or score_col not in df.columns:
        return out
    target = df.loc[idx[0]]

    for kind, label in (("sector", "sector"), ("industry", "industry")):
        group_val = target.get(kind)
        if pd.isna(group_val) or group_val == "":
            continue
        peers = df[df[kind] == group_val]
        if peers.empty:
            continue
        ranks = peers[score_col].rank(ascending=False, method="min", na_option="bottom")
        try:
            r = int(ranks.loc[idx[0]])
        except KeyError:
            continue
        out[f"{label}_rank"] = r
        out[f"{label}_total"] = int(len(peers))
    return out


def compute_indicator_percentiles(
    df: pd.DataFrame, group: IndicatorGroup
) -> dict[str, pd.Series]:
    """Globaler Perzentil-Rang (0..100) je Indikator-Spalte einer Gruppe.

    Verwendet ``Series.rank(pct=True)`` und invertiert für Indikatoren mit
    ``lower_better=True`` (analog ``scoring._indicator_percentile``).
    """
    out: dict[str, pd.Series] = {}
    for it in group.items:
        if it.key not in df.columns:
            continue
        ranks = _clean_series(df, it.key).rank(
            pct=True, method="average", na_option="keep"
        )
        if it.lower_better:
            ranks = 1 - ranks
        out[it.key] = ranks * 100.0
    return out


def _factor_label_from_key(key: str) -> str:
    return {
        "value_score": "Value",
        "quality_score": "Quality",
        "growth_score": "Growth",
        "momentum_score": "Momentum",
        "lowvol_score": "Low Vol",
    }.get(key, key)


# Settings-Schlüssel je Faktor — wird sowohl für das Faktor-Profil (links im
# PDF) als auch für die Indikator-Gruppen-Header (rechts im PDF) verwendet,
# damit beide Anzeigen aus derselben Quelle kommen.
_FACTOR_SETTINGS_KEY: dict[str, str] = {
    "Value": "value",
    "Quality": "quality",
    "Growth": "growth",
    "Momentum": "momentum",
    "Low Vol": "lowvol",
    "Low Volatility": "lowvol",
}

# Fallback-Defaults (entspricht ``Settings.factor_weights`` Default × 100),
# falls die Settings keine Faktor-Gewichte enthalten.
_FACTOR_WEIGHT_FALLBACK: dict[str, int] = {
    "value": 25,
    "quality": 27,
    "growth": 15,
    "momentum": 18,
    "lowvol": 15,
}


def _factor_weight_pct(settings: Settings, key_or_label: str) -> int:
    """Liefert das aktuelle Faktor-Gewicht in Prozent (gerundet auf ganze %).

    ``key_or_label`` darf entweder ein Settings-Key (``value`` …) oder das
    Anzeige-Label (``Value`` …) sein. Settings ``factor_weights`` enthält
    Dezimalanteile (0,25 = 25 %); diese werden hier auf ganze Prozent
    gerundet, damit das PDF konsistent zu den UI-Reglern (step=0.01)
    rendert.
    """
    settings_key = _FACTOR_SETTINGS_KEY.get(key_or_label, key_or_label)
    fallback = _FACTOR_WEIGHT_FALLBACK.get(settings_key, 0)
    fw = getattr(settings, "factor_weights", None) if settings is not None else None
    if not isinstance(fw, dict):
        return fallback
    raw = fw.get(settings_key)
    try:
        return int(round(float(raw) * 100))
    except (TypeError, ValueError):
        return fallback


def generate_thesis(row: pd.Series) -> str:
    """Deterministische deutsche 1–2-Satz-These aus den Faktor-Scores.

    Wählt den stärksten und schwächsten Faktor und ergänzt eine SMA- bzw.
    Filter-Notiz. Beispiel:

        »Starkes Quality- und Growth-Profil mit überdurchschnittlichen
        Faktor-Werten. Value liegt unter dem Modell-Mittel.
        Trendbestätigung durch Golden Cross.«
    """
    factor_keys = [
        "value_score",
        "quality_score",
        "growth_score",
        "momentum_score",
        "lowvol_score",
    ]
    scores: list[tuple[str, float]] = []
    for k in factor_keys:
        v = _safe_float(row.get(k))
        if v is not None:
            scores.append((k, v))
    if not scores:
        return "Keine ausreichenden Daten für eine Faktor-These."

    sorted_scores = sorted(scores, key=lambda kv: kv[1], reverse=True)
    top = sorted_scores[:2]
    weak = sorted_scores[-1]

    top_labels = [_factor_label_from_key(k) for k, _ in top if _ >= 60]
    weak_label = _factor_label_from_key(weak[0])
    weak_score = weak[1]

    parts: list[str] = []

    if len(top_labels) >= 2 and top[0][1] >= 70:
        parts.append(
            f"Starkes {top_labels[0]}- und {top_labels[1]}-Profil mit "
            f"überdurchschnittlichen Faktor-Werten."
        )
    elif top_labels and top[0][1] >= 70:
        parts.append(
            f"{top_labels[0]} dominiert das Faktor-Profil deutlich; "
            f"andere Dimensionen liegen im Mittelfeld."
        )
    else:
        parts.append("Faktor-Profil im Mittelfeld ohne ausgeprägte Stärke.")

    if weak_score < 40 and _factor_label_from_key(weak[0]) not in top_labels:
        parts.append(f"{weak_label} liegt unter dem Modell-Mittel.")

    sma = str(row.get("sma_signal") or "")
    sma_u = sma.upper()
    if "GOLDEN" in sma_u:
        parts.append("Trendbestätigung durch Golden Cross.")
    elif "DEATH" in sma_u:
        parts.append("Trendwarnung durch Death Cross.")
    elif ">" in sma:
        parts.append("Kurs notiert über der 200-Tage-Linie.")
    elif "<" in sma:
        parts.append("Kurs notiert unter der 200-Tage-Linie.")

    filter_ok = str(row.get("filter_ok") or "")
    if filter_ok == "NEIN":
        parts.append("Qualitätsfilter (Piotroski/Altman/Marktkap.) nicht bestanden.")

    return " ".join(parts).strip()


# ---------------------------------------------------------------------------
# Context builder


def build_context(
    ticker: str,
    df: pd.DataFrame,
    settings: Settings,
    *,
    show_peers: bool = True,
    dense: bool = False,
    today: date | None = None,
) -> dict[str, Any]:
    """Bündelt alle Werte für die Jinja-Vorlage."""

    if df is None or df.empty:
        raise ValueError("Keine Daten zum Rendern (STATE.scored ist leer).")

    from .uid import row_by_uid

    r = row_by_uid(df, ticker)
    if r is None:
        raise ValueError(f"Ticker {ticker!r} nicht im Universum.")

    today = today or date.today()
    months_de = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    generated_on = f"{today.day:02d}. {months_de[today.month - 1]} {today.year}"

    classification = str(r.get("classification") or "").strip()
    accent = _classify_accent(classification)

    # Faktor-Profil — Gewichte dynamisch aus den App-Einstellungen, damit
    # Änderungen in /einstellungen sich im PDF widerspiegeln.
    factor_defs = [
        ("Value", "value_score", "#5b8def"),
        ("Quality", "quality_score", "#22a06b"),
        ("Growth", "growth_score", "#d97757"),
        ("Momentum", "momentum_score", "#8b5cf6"),
        ("Low Vol", "lowvol_score", "#0891b2"),
    ]
    factors_ctx: list[dict[str, Any]] = []
    for label, key, color in factor_defs:
        v = _safe_float(r.get(key))
        score_pct = max(0.0, min(100.0, v)) if v is not None else 0.0
        factors_ctx.append(
            {
                "label": label,
                "color": color,
                "weight": _factor_weight_pct(settings, label),
                "score_pct": round(score_pct, 1),
                "score_text": fmt_de(v, 0) if v is not None else "–",
            }
        )

    # Indikator-Gruppen mit Perzentil-Bars
    groups_ctx: list[dict[str, Any]] = []
    for grp in INDICATOR_GROUPS:
        pct_map = compute_indicator_percentiles(df, grp)
        items_ctx: list[dict[str, Any]] = []
        for it in grp.items:
            raw = r.get(it.key)
            value_str = _format_indicator_value(it.key, raw)
            pct_series = pct_map.get(it.key)
            pct_val: float = 0.0
            if pct_series is not None:
                try:
                    p = pct_series.loc[r.name]
                    if pd.notna(p):
                        pct_val = float(p)
                except KeyError:
                    pass
            items_ctx.append(
                {
                    "label": it.label,
                    "value": value_str,
                    "pct": round(max(0.0, min(100.0, pct_val)), 1),
                }
            )
        groups_ctx.append(
            {
                "name": grp.name,
                # Gewicht aus den App-Einstellungen — INDICATOR_GROUPS.weight_pct
                # ist nur noch Anzeige-Default für Stellen, die keine Settings
                # haben (z.B. Tests). Der PDF-Export nutzt immer Settings.
                "weight": _factor_weight_pct(settings, grp.name),
                "color": grp.color,
                "rows": items_ctx,
            }
        )

    # Filter-Badges
    piotr = _safe_float(r.get("piotroski"))
    altman = _safe_float(r.get("altman_z"))
    filt = str(r.get("filter_ok") or "-")
    filter_badges = [
        {
            "label": "Piotroski",
            "value": f"{int(piotr)} / 9" if piotr is not None else "–",
            "tone": "up" if (piotr is not None and piotr >= settings.min_piotroski) else "down",
        },
        {
            "label": "Altman Z",
            "value": fmt_de(altman, 2) if altman is not None else "–",
            "tone": "up" if (altman is not None and altman >= settings.min_altman_z) else "down",
        },
        {
            "label": "Filter",
            "value": "bestanden" if filt == "JA" else ("nicht bestanden" if filt == "NEIN" else "–"),
            "tone": "up" if filt == "JA" else ("down" if filt == "NEIN" else "neutral"),
        },
    ]

    # Returns
    returns_ctx: list[dict[str, str]] = []
    for label, key in (("1M", "ret_1m"), ("3M", "ret_3m"), ("6M", "ret_6m"), ("12M", "ret_12m")):
        v = _safe_float(r.get(key))
        returns_ctx.append(
            {
                "label": label,
                "value": fmt_signed_percent(v, 1) if v is not None else "–",
                "tone": _ret_tone(v),
            }
        )

    # 52W-Range-Position
    last = _safe_float(r.get("last_price"))
    low = _safe_float(r.get("low_52w"))
    high = _safe_float(r.get("high_52w"))
    range_pct: float | None = None
    if last is not None and low is not None and high is not None and high > low:
        range_pct = round(max(0.0, min(100.0, (last - low) / (high - low) * 100.0)), 1)

    # Rang
    rank = compute_rank(df, ticker)

    # Peers (4 ähnlichste) — bei aktivem Scoring v2 im v2-Faktorraum
    # bestimmt und mit Composite-v2-Score/-Klasse beschriftet.
    v2_primary = (
        settings.scoring_version == "v2" and "composite_score" in df.columns
    )
    peer_score_col = "composite_score" if v2_primary else "total_score"
    peer_class_col = "classification_v2" if v2_primary else "classification"
    peers_ctx: list[dict[str, Any]] = []
    if show_peers:
        peers_df = compute_peers(
            df, ticker, n=4, mode="similar",
            version="v2" if v2_primary else "v1",
        )
        for _, p in peers_df.iterrows():
            score = _safe_float(p.get(peer_score_col))
            ret_12m = _safe_float(p.get("ret_12m"))
            peers_ctx.append(
                {
                    "ticker": str(p.get("ticker") or ""),
                    "name": str(p.get("name") or ""),
                    "score": fmt_de(score, 1) if score is not None else "–",
                    "classification": str(p.get(peer_class_col) or ""),
                    "ret_12m": fmt_signed_percent(ret_12m, 1) if ret_12m is not None else "–",
                    "ret_tone": _ret_tone(ret_12m),
                }
            )

    # Empfehlung
    recommendation = str(r.get("recommendation") or "-")

    # Beta / Vol / Marktkap
    beta = _safe_float(r.get("beta"))
    vol_1y = _safe_float(r.get("volatility_1y"))
    mcap = _safe_float(r.get("market_cap"))

    # Composite v2 (nur wenn berechnet)
    v2_ctx: dict[str, Any] | None = None
    comp_score = _safe_float(r.get("composite_score"))
    if comp_score is not None:
        rank_v2 = compute_rank(df, ticker, score_col="composite_score")
        zone = str(r.get("zone_v2") or "–")
        cls_v2 = str(r.get("classification_v2") or "–")
        cov = _safe_float(r.get("data_coverage_v2"))
        v2_factors: list[dict[str, Any]] = []
        for key, label in (
            ("value", "Value"),
            ("quality", "Quality"),
            ("momentum", "Momentum"),
            ("investment", "Investment"),
        ):
            z = _safe_float(r.get(f"z_{key}"))
            fcov = _safe_float(r.get(f"cov_{key}"))
            v2_factors.append(
                {
                    "label": label,
                    "z": fmt_de(z, 2) if z is not None else "–",
                    "z_val": z if z is not None else 0.0,
                    "coverage": (
                        fmt_de(fcov * 100, 0) + " %" if fcov is not None else "–"
                    ),
                }
            )
        reasons = r.get("filter_reasons")
        reasons_str = (
            ", ".join(str(x) for x in reasons)
            if isinstance(reasons, (list, tuple)) and len(reasons)
            else ""
        )
        v2_ctx = {
            "score": fmt_de(comp_score, 1),
            "composite_z": fmt_de(_safe_float(r.get("composite_z")), 2),
            "classification": cls_v2,
            "class_accent": _classify_accent_v2(cls_v2),
            "zone": zone,
            "zone_accent": _zone_accent(zone),
            "coverage": fmt_de(cov * 100, 0) + " %" if cov is not None else "–",
            "filter_pass": bool(r.get("filter_pass")),
            "filter_reasons": reasons_str,
            "trend_warning": bool(r.get("trend_warning")),
            "factors": v2_factors,
            "sector_rank": rank_v2["sector_rank"],
            "sector_total": rank_v2["sector_total"],
        }

    return {
        "v2": v2_ctx,
        "ticker": str(r.get("ticker") or ticker),
        "name": str(r.get("name") or ""),
        "sector": str(r.get("sector") or ""),
        "industry": str(r.get("industry") or ""),
        "region": str(r.get("region") or ""),
        "total_score": fmt_de(_safe_float(r.get("total_score")), 1),
        "classification": classification or "–",
        "accent_color": accent,
        "thesis": generate_thesis(r),
        "last_price": fmt_de(last, 2) if last is not None else "–",
        "market_cap": fmt_market_cap(mcap) if mcap is not None else "–",
        "range_low": fmt_de(low, 2) if low is not None else "–",
        "range_high": fmt_de(high, 2) if high is not None else "–",
        "range_pct": range_pct,
        "beta": fmt_de(beta, 2) if beta is not None else "–",
        "volatility_1y": fmt_percent(vol_1y, 1) if vol_1y is not None else "–",
        "recommendation": recommendation,
        "recommendation_tone": _recommendation_tone(recommendation),
        "filter_ok": filt,
        "sector_rank": rank["sector_rank"] if rank["sector_rank"] is not None else "–",
        "sector_total": rank["sector_total"],
        "industry_rank": rank["industry_rank"],
        "industry_total": rank["industry_total"],
        "factors": factors_ctx,
        "indicator_groups": groups_ctx,
        "filter_badges": filter_badges,
        "sma_signal": str(r.get("sma_signal") or "–"),
        "sma_tone": _sma_tone(r.get("sma_signal")),
        "sma_50": fmt_de(_safe_float(r.get("sma_50")), 2) if _safe_float(r.get("sma_50")) is not None else "–",
        "sma_200": fmt_de(_safe_float(r.get("sma_200")), 2) if _safe_float(r.get("sma_200")) is not None else "–",
        "returns": returns_ctx,
        "peers": peers_ctx,
        "generated_on": generated_on,
        "dense": dense,
    }


def _format_indicator_value(key: str, value: Any) -> str:
    """Anzeige-Wert je Indikator-Spalte. Spiegelt ``fmt_indicator``-Logik wider,
    nutzt aber lokale Defaults damit es ohne ``app.core.schema`` reicht."""
    from app.ui.formatters import fmt_indicator
    return fmt_indicator(key, value)


# ---------------------------------------------------------------------------
# Persistenter WeasyPrint-Worker
#
# Statt für jeden PDF-Klick einen frischen Python-Subprozess mit WeasyPrint-
# Import (≈ 8 s Cold-Start) zu starten, halten wir genau einen Worker am
# Leben und sprechen ihn über stdin/stdout an. Das Render-Latenz-Profil:
#   1. Klick: 8–9 s (Worker-Boot + WeasyPrint-Import + Render)
#   2. Klick und folgend: <1 s (nur Render)


_NIX_STORE_ROOTS: tuple[str, ...] = (
    # Replit überlagert den ECHTEN Nix-Store unter /mnt/pid1/nix/store —
    # /nix/store selbst kann auf manchen Sandboxen extrem langsam zu globben
    # sein, daher nur sondieren, wenn das Replit-Mount nichts liefert.
    "/mnt/pid1/nix/store",
)


def _discover_nix_lib_paths() -> list[str]:
    """Sucht im Replit/Nix-Store die für WeasyPrint nötigen lib-Verzeichnisse.

    Liefert eine leere Liste, wenn kein Nix-Store da ist (lokale Entwicklung
    auf Mac/Linux mit System-Libs) — dann werden keine Pfade gesetzt und
    WeasyPrint nutzt die Standard-Linker-Suche.
    """
    suffixes = (
        "*-libffi-*/lib",
        "*-glib-2.84*/lib",
        "*-cairo-*/lib",
        "*-harfbuzz-*/lib",
        "*-pango-*/lib",
        "*-gdk-pixbuf-*/lib",
        "*-fontconfig-*-lib/lib",
    )
    out: list[str] = []
    seen: set[str] = set()
    for root in _NIX_STORE_ROOTS:
        if not os.path.isdir(root):
            continue
        for suf in suffixes:
            for p in glob.glob(f"{root}/{suf}"):
                if p not in seen:
                    out.append(p)
                    seen.add(p)
    return out


_WORKER_SCRIPT = Path(__file__).resolve().parent / "_factsheet_worker.py"
_worker_proc: subprocess.Popen | None = None
_worker_lock = threading.Lock()


class FactsheetRenderError(RuntimeError):
    """WeasyPrint-Subprozess konnte das PDF nicht erzeugen."""


def _start_worker() -> subprocess.Popen:
    env = dict(os.environ)
    nix_libs = _discover_nix_lib_paths()
    if nix_libs:
        existing = env.get("LD_LIBRARY_PATH", "")
        env["LD_LIBRARY_PATH"] = ":".join(nix_libs) + (":" + existing if existing else "")
    return subprocess.Popen(
        [sys.executable, "-u", str(_WORKER_SCRIPT)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        # WeasyPrint schreibt gelegentlich Font-Fallback-Warnungen auf stderr.
        # DEVNULL verhindert Buffer-Deadlocks (niemand liest sie sonst).
        stderr=subprocess.DEVNULL,
        env=env,
        bufsize=0,
    )


def _ensure_worker() -> subprocess.Popen:
    global _worker_proc
    if _worker_proc is None or _worker_proc.poll() is not None:
        _worker_proc = _start_worker()
    return _worker_proc


def _shutdown_worker() -> None:
    global _worker_proc
    proc = _worker_proc
    _worker_proc = None
    if proc is None:
        return
    with suppress(Exception):
        if proc.stdin is not None:
            proc.stdin.close()
    with suppress(Exception):
        proc.wait(timeout=3)
    if proc.poll() is None:
        with suppress(Exception):
            proc.kill()


atexit.register(_shutdown_worker)


def _read_exact(stream, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = stream.read(n - len(out))
        if not chunk:
            raise EOFError("Worker hat die Verbindung geschlossen")
        out.extend(chunk)
    return bytes(out)


def _run_weasyprint(html: str, base_url: str) -> bytes:
    html_b = html.encode("utf-8")
    url_b = base_url.encode("utf-8")

    with _worker_lock:
        # Bei Worker-Crash genau einmal neu starten und Job wiederholen.
        last_error: Exception | None = None
        for attempt in range(2):
            proc = _ensure_worker()
            assert proc.stdin is not None and proc.stdout is not None
            try:
                proc.stdin.write(struct.pack(">I", len(html_b)))
                proc.stdin.write(html_b)
                proc.stdin.write(struct.pack(">I", len(url_b)))
                proc.stdin.write(url_b)
                proc.stdin.flush()

                status = _read_exact(proc.stdout, 1)
                paylen = struct.unpack(">I", _read_exact(proc.stdout, 4))[0]
                payload = _read_exact(proc.stdout, paylen)
            except (BrokenPipeError, EOFError, OSError) as exc:
                # Worker tot — beim nächsten Versuch neu starten.
                last_error = exc
                _shutdown_worker()
                continue

            if status == b"\x00":
                if not payload.startswith(b"%PDF-"):
                    raise FactsheetRenderError("Worker lieferte ungültigen PDF-Header.")
                return payload
            err_msg = payload.decode("utf-8", errors="replace")
            raise FactsheetRenderError(f"WeasyPrint im Worker fehlgeschlagen: {err_msg}")

        raise FactsheetRenderError(
            f"PDF-Worker konnte nicht erreicht werden: {last_error!s}"
        )


# ---------------------------------------------------------------------------
# Public entry point


def render_editorial_factsheet(
    ticker: str,
    df: pd.DataFrame,
    settings: Settings,
    *,
    show_peers: bool = True,
    dense: bool = False,
    today: date | None = None,
) -> bytes:
    """Erzeugt PDF-Bytes für ein Editorial-Factsheet zum gegebenen Ticker."""

    ctx = build_context(
        ticker, df, settings, show_peers=show_peers, dense=dense, today=today
    )
    template = _jinja_env.get_template("editorial.html.j2")
    html = template.render(**ctx)
    return _run_weasyprint(html, base_url=str(_TEMPLATE_DIR) + "/")
