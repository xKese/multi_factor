"""Investment-Komitee-Bericht — Dash-freier Builder (Datensammlung).

Sammelt den kompletten Modellstand in ein Ergebnis-Dict, das die Renderer
(``committee_report_pdf`` → PDF/HTML, ``committee_report_xlsx`` → Excel) und
die Dash-Seite ``/bericht`` konsumieren:

- Universum (Zonen, Klassen, Top-Kandidaten) und **Veränderungen** gegen den
  letzten Import und gegen den Snapshot ~1 Monat zuvor (PIT-Archiv
  ``koyfin_universe_history``): Mover, Zonenwechsel, neue/ausgeschiedene
  Kandidaten, Ranking-Stabilität, Watchlist nahe Einstieg/Ausstieg.
- Sektoren (Score, Δ, Returns, Breadth, Rotations-Quadrant).
- Aktives Portfolio mit Scores der Einzeltitel, Flags, Sofortmaßnahmen,
  Signalwechseln, Exposures und Konzentration.
- Modellportfolio (jüngster gespeicherter Lauf) mit rekonstruierter
  Trade-Liste, Exposures und aktiven Overrides.
- Optional Factor Timing, Risiko & Benchmark (Kurscache) und Agenten-Ratings.
- Datenqualität/Diagnosen und Parameter-Anhang.

Der Builder liefert **Rohwerte** (floats, Dezimalanteile, DataFrames) — die
deutsche Formatierung übernehmen die Renderer. Fehlende optionale Daten
führen nie zu einer Exception: der Abschnitt trägt ``available = False`` und
eine ``note``. Importiert bewusst kein Dash (``app.ui.formatters`` und
``app.ui.labels`` sind Dash-frei; ``app.ui`` selbst nicht).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from app.core import persistence
from app.core.config import PC_PROFILE_FIELDS, Settings
from app.core.diagnostics import (
    SEV_ERROR,
    SEV_INFO,
    SEV_WARNING,
    Diagnostic,
    count_by_severity,
    sort_diagnostics,
)
from app.core.momentum import MOMENTUM_DEATH, MOMENTUM_GOLDEN
from app.core.portfolio import build_flags
from app.core.schema import KOYFIN_COLUMNS, OPTIONAL_COLUMNS
from app.core.scoring_v2 import (
    V2_FACTOR_NAMES,
    ZONE_CANDIDATE,
    ZONE_FILTER,
    ZONE_HOLD,
    ZONE_SELL,
)
from app.core.signal_events import snapshot_date_from_universe

log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_TOP_N",
    "MIN_ABS_DELTA_MOVER",
    "MONTH_TARGET_LAG_DAYS",
    "MONTH_TOLERANCE_DAYS",
    "REPORT_TITLE",
    "ReportOptions",
    "build_committee_report",
    "compute_stock_deltas",
    "generate_key_findings",
    "normalize_snapshot_frame",
    "pick_comparison_snapshots",
    "report_filename_stem",
    "resolve_score_columns",
    "rrg_quadrant",
    "zone_transition_matrix",
]

REPORT_TITLE = "Investment-Komitee-Bericht"

# Vergleichsbasis „~1 Monat": Ziel-Lag und Toleranz wie der Sektor-Momentum-
# ΔScore (``sector_momentum.HISTORY_TARGET_LAG_DAYS`` / ``_TOLERANCE_DAYS``).
MONTH_TARGET_LAG_DAYS = 30
MONTH_TOLERANCE_DAYS = 15
# Score-Punkte (Perzentil × 100), ab denen ein Titel als „Mover" gilt.
MIN_ABS_DELTA_MOVER = 5.0
TOP_MOVERS = 10
DEFAULT_TOP_N = 25
# Watchlist: Abstand (Perzentilpunkte, Dezimal) zur Einstiegs-/Ausstiegsschwelle.
WATCHLIST_BAND = 0.02
# Overrides, die innerhalb dieser Frist auslaufen, gelten als entscheidungsbedürftig.
OVERRIDE_DUE_DAYS = 30
# Import-Alter in Tagen, ab dem der Datenstand als veraltet gilt.
STALE_IMPORT_DAYS = 7
# Agenten-Ratings älter als diese Frist gelten als überholt.
AGENT_STALE_DAYS = 90
# Schwellen der Ranking-Stabilität (Key Findings).
RANK_STABILITY_WARN_RHO = 0.8
ZONE_TURNOVER_WARN = 0.20
MAX_KEY_FINDINGS = 12

ZONES: tuple[str, ...] = (ZONE_CANDIDATE, ZONE_HOLD, ZONE_SELL, ZONE_FILTER)
V1_RECOMMENDATIONS: tuple[str, ...] = (
    "STRONG BUY",
    "BUY",
    "HOLD",
    "SELL",
    "Filter nicht bestanden",
)

RRG_LEADING = "Leading"
RRG_WEAKENING = "Weakening"
RRG_IMPROVING = "Improving"
RRG_LAGGING = "Lagging"
RRG_NONE = "–"

STATUS_BOTH = "beide"
STATUS_NEW = "neu im Universum"
STATUS_GONE = "nicht mehr im Universum"

# Agenten-Rating → Ton (identisch zu ``app.ui.agent_report._RATING_TONE``,
# hier dupliziert, weil jenes Modul Dash importiert).
AGENT_RATING_TONE: dict[str, str] = {
    "Buy": "up",
    "Overweight": "up",
    "Hold": "warn",
    "Underweight": "down",
    "Sell": "down",
}

# Deutsche Labels der Settings-Felder im Parameter-Anhang.
SETTINGS_LABELS: dict[str, str] = {
    "scoring_version": "Scoring-Version",
    "factor_timing_mode": "Faktor-Timing-Modus",
    "v2_weight_value": "Gewicht Value",
    "v2_weight_quality": "Gewicht Quality",
    "v2_weight_momentum": "Gewicht Momentum",
    "v2_weight_investment": "Gewicht Investment",
    "v2_min_factor_weight": "Mindestanteil vorhandener Faktorgewichte",
    "v2_min_group_size_l1": "Mindestgruppengröße Ebene 1",
    "v2_min_group_size_l2": "Mindestgruppengröße Ebene 2",
    "v2_min_group_valid": "Mindestanzahl gültiger Werte je Gruppe",
    "v2_winsor_lower": "Winsorisierung unten (Indikator)",
    "v2_winsor_upper": "Winsorisierung oben (Indikator)",
    "v2_zscore_cap": "Z-Score-Cap",
    "v2_composite_winsor_lower": "Winsorisierung unten (Composite)",
    "v2_composite_winsor_upper": "Winsorisierung oben (Composite)",
    "v2_min_volatility": "Vola-Floor Momentum",
    "v2_neut_scheme_default": "Neutralisierungsschema (Standard)",
    "filter_min_market_cap": "Mindest-Marktkapitalisierung (Mio.)",
    "filter_min_piotroski": "Mindest-Piotroski",
    "filter_min_altman": "Mindest-Altman-Z",
    "filter_min_adv": "Mindest-Tagesumsatz (Mio.)",
    "filter_min_coverage": "Mindest-Datenabdeckung",
    "filter_min_listing_days": "Mindest-Listing (Tage)",
    "filter_max_de": "Max. Debt/Equity",
    "filter_min_icr": "Mindest-Zinsdeckung",
    "pc_target_n": "Zielanzahl Titel",
    "pc_min_n": "Mindestanzahl Titel",
    "pc_max_n": "Maximalanzahl Titel",
    "pc_entry_pct": "Einstiegsschwelle (Perzentil)",
    "pc_exit_pct": "Ausstiegsschwelle (Perzentil)",
    "pc_fill_pct": "Auffüllschwelle (Perzentil)",
    "pc_sector_band": "Sektor-Bandbreite",
    "pc_region_band": "Regions-Bandbreite",
    "pc_max_per_sector": "Max. Titel je Sektor",
    "pc_benchmark_source": "Benchmark-Quelle",
    "pc_benchmark_max_age_days": "Max. Alter Benchmark (Tage)",
    "pc_vol_floor": "Vola-Floor (Gewichtung)",
    "pc_vol_cap": "Vola-Cap (Gewichtung)",
    "pc_weight_cap": "Gewichts-Cap",
    "pc_weight_floor": "Gewichts-Floor",
    "pc_te_target_low": "TE-Zielband unten",
    "pc_te_target_high": "TE-Zielband oben",
    "pc_te_max": "TE-Maximum",
    "pc_max_cte_share": "Max. CTE-Anteil je Titel",
    "pc_te_min_coverage": "Mindest-Kursabdeckung TE",
    "pc_rebalance_months": "Rebalancing-Monate (voll)",
    "pc_interim_months": "Rebalancing-Monate (Zwischenlauf)",
    "pc_turnover_budget_full": "Turnover-Budget (voll)",
    "pc_turnover_budget_interim": "Turnover-Budget (Zwischenlauf)",
    "pc_min_trade_size": "Mindest-Trade-Größe",
    "buy_threshold": "BUY-Schwelle (v1)",
    "sell_threshold": "SELL-Schwelle (v1)",
    "percentile_mode": "Perzentil-Modus (v1)",
    "min_piotroski": "Mindest-Piotroski (v1)",
    "min_altman_z": "Mindest-Altman-Z (v1)",
    "min_market_cap": "Mindest-Marktkapitalisierung (v1)",
    "risk_benchmark_symbol": "Benchmark (Risiko)",
    "risk_report_dir": "Report-Verzeichnis",
}

# Settings-Felder, deren Wert ein Dezimalanteil ist (Renderer → Prozent).
PERCENT_SETTINGS: frozenset[str] = frozenset(
    {
        "v2_weight_value",
        "v2_weight_quality",
        "v2_weight_momentum",
        "v2_weight_investment",
        "v2_min_factor_weight",
        "v2_winsor_lower",
        "v2_winsor_upper",
        "v2_composite_winsor_lower",
        "v2_composite_winsor_upper",
        "v2_min_volatility",
        "filter_min_coverage",
        "pc_entry_pct",
        "pc_exit_pct",
        "pc_fill_pct",
        "pc_sector_band",
        "pc_region_band",
        "pc_vol_floor",
        "pc_vol_cap",
        "pc_weight_cap",
        "pc_weight_floor",
        "pc_te_target_low",
        "pc_te_target_high",
        "pc_te_max",
        "pc_max_cte_share",
        "pc_te_min_coverage",
        "pc_turnover_budget_full",
        "pc_turnover_budget_interim",
        "pc_min_trade_size",
    }
)


@dataclass
class ReportOptions:
    """Optionen des Berichts (Seite/CLI → Builder)."""

    portfolio_id: int | None = None  # None → aktives Portfolio
    prev_snapshot: date | None = None  # Override, sonst automatisch
    month_snapshot: date | None = None
    include_sectors: bool = True
    include_model_portfolio: bool = True
    include_factor_timing: bool = True
    include_risk: bool = False  # opt-in (teuer, benötigt Kurscache)
    include_agents: bool = True
    include_diagnostics: bool = True
    include_parameters: bool = True
    top_n: int = DEFAULT_TOP_N
    prepared_by: str = ""


# ── Kleine Helfer ──────────────────────────────────────────────────────────


def _unavailable(note: str) -> dict:
    return {"available": False, "note": note}


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    """Numerische Spalte (NaN-Serie, wenn sie fehlt)."""
    if column in df.columns:
        return pd.to_numeric(df[column], errors="coerce")
    return pd.Series(np.nan, index=df.index, dtype=float)


def _text(df: pd.DataFrame, column: str) -> pd.Series:
    if column in df.columns:
        return df[column].astype(object).where(df[column].notna(), None)
    return pd.Series([None] * len(df), index=df.index, dtype=object)


def _bool(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    s = df[column]
    if s.dtype == bool:
        return s
    return s.map(
        lambda v: str(v).strip().lower() in ("1", "true", "wahr", "ja")
        if v is not None and not (isinstance(v, float) and pd.isna(v))
        else False
    ).astype(bool)


def _float_or_none(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if np.isnan(f) else f


def _mean(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.mean()) if not s.empty else None


def _median(series: pd.Series) -> float | None:
    s = pd.to_numeric(series, errors="coerce").dropna()
    return float(s.median()) if not s.empty else None


def _uid_index(df: pd.DataFrame) -> pd.DataFrame:
    """Frame uid-indiziert (eindeutig), ``uid`` bleibt als Spalte erhalten."""
    out = df.copy()
    key = "uid" if "uid" in out.columns else "ticker"
    out["uid"] = out[key].astype(str)
    out = out.drop_duplicates("uid")
    out.index = pd.Index(out["uid"].astype(str), name="_uid")
    return out


def _records(df: pd.DataFrame | None, columns: list[str] | None = None) -> list[dict]:
    """DataFrame → Liste von Dicts (NaN → None), optional auf Spalten begrenzt."""
    if df is None or df.empty:
        return []
    out = df
    if columns:
        out = out[[c for c in columns if c in out.columns]]
    recs = out.to_dict("records")
    cleaned: list[dict] = []
    for r in recs:
        cleaned.append(
            {
                k: (None if (isinstance(v, float) and np.isnan(v)) else v)
                for k, v in r.items()
            }
        )
    return cleaned


def _list_join(value) -> str:
    if isinstance(value, (list, tuple)):
        return " · ".join(str(v) for v in value)
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value)


# ── Score-Spalten (v1/v2) ──────────────────────────────────────────────────


def resolve_score_columns(settings: Settings, df: pd.DataFrame | None) -> dict:
    """Spalten der Primäranzeige (ohne Zugriff auf ``STATE``).

    v2, wenn ``scoring_version == "v2"`` und ``composite_score`` vorliegt;
    sonst v1 (``total_score``/``classification``/``recommendation``). Kein
    Abschnitt greift hart auf v2-Spalten zu — alle nutzen dieses Mapping.
    """
    has_v2 = df is not None and not df.empty and "composite_score" in df.columns
    if str(getattr(settings, "scoring_version", "v2")) == "v2" and has_v2:
        return {
            "version": "v2",
            "score": "composite_score",
            "z": "composite_z",
            "pct": "composite_pct",
            "klass": "classification_v2",
            "zone": "zone_v2",
            "zone_values": ZONES,
            "good_zones": {ZONE_CANDIDATE},
            "hold_zones": {ZONE_HOLD},
            "bad_zones": {ZONE_SELL},
            "filter_zones": {ZONE_FILTER},
            "score_label": "Composite v2",
            "zone_label": "Zone",
        }
    return {
        "version": "v1",
        "score": "total_score",
        "z": None,
        "pct": None,
        "klass": "classification",
        "zone": "recommendation",
        "zone_values": V1_RECOMMENDATIONS,
        "good_zones": {"STRONG BUY", "BUY"},
        "hold_zones": {"HOLD"},
        "bad_zones": {"SELL"},
        "filter_zones": {"Filter nicht bestanden"},
        "score_label": "Gesamt-Score (v1)",
        "zone_label": "Empfehlung",
    }


def _zone_series(df: pd.DataFrame, cols: dict) -> pd.Series:
    return _text(df, cols["zone"]).map(lambda v: str(v) if v is not None else None)


def _is_eligible(df: pd.DataFrame, cols: dict) -> pd.Series:
    if cols["version"] == "v2" and "filter_pass" in df.columns:
        return _bool(df, "filter_pass")
    zone = _zone_series(df, cols)
    return ~zone.isin(cols["filter_zones"]) & zone.notna()


# ── Snapshot-Auswahl & -Laden ──────────────────────────────────────────────


def pick_comparison_snapshots(
    snapshots: list[tuple[date, int]],
    asof: date,
    *,
    prev_override: date | None = None,
    month_override: date | None = None,
    target_lag_days: int = MONTH_TARGET_LAG_DAYS,
    tolerance_days: int = MONTH_TOLERANCE_DAYS,
) -> dict:
    """Wählt Vorimport- und ~1-Monats-Snapshot aus dem Archiv.

    ``prev`` = jüngstes Archivdatum < ``asof`` (der aktuelle Import ist
    selbst der jüngste Snapshot und wird nie gegen sich selbst verglichen).
    ``month`` = Datum mit minimalem Abstand zu ``asof − target_lag_days``
    (Tie → älteres Datum), nur wenn der Abstand ≤ ``tolerance_days``.
    Overrides müssen im Archiv liegen und vor ``asof`` datieren.
    """
    counts: dict[date, int] = {}
    for d, n in snapshots:
        if isinstance(d, datetime):
            d = d.date()
        counts[d] = int(n)
    older = sorted(d for d in counts if d < asof)

    prev: date | None
    prev_note = ""
    if prev_override is not None and prev_override in counts and prev_override < asof:
        prev = prev_override
    else:
        if prev_override is not None:
            prev_note = (
                f"Gewählter Vorimport-Snapshot {prev_override.isoformat()} nicht "
                "im Archiv — automatisch gewählt. "
            )
        prev = older[-1] if older else None
        if prev is None:
            prev_note += "Kein früherer Snapshot im Archiv — kein Vergleich zum Vorimport."

    target = asof - timedelta(days=int(target_lag_days))
    month: date | None = None
    offset: int | None = None
    month_note = ""
    if month_override is not None and month_override in counts and month_override < asof:
        month = month_override
        offset = abs((month_override - target).days)
    elif older:
        if month_override is not None:
            month_note = (
                f"Gewählter 1M-Snapshot {month_override.isoformat()} nicht im "
                "Archiv — automatisch gewählt. "
            )
        best = min(older, key=lambda d: (abs((d - target).days), d))
        offset = abs((best - target).days)
        if offset <= int(tolerance_days):
            month = best
        else:
            month_note += (
                f"Nächster Snapshot ({best.isoformat()}) liegt {offset} Tage vom "
                f"Ziel {target.isoformat()} entfernt (> {int(tolerance_days)}) — "
                "kein 1M-Vergleich."
            )
    else:
        month_note = "Kein früherer Snapshot im Archiv — kein 1M-Vergleich."

    same = prev is not None and month == prev
    if same:
        month_note = (
            "Der ~1M-Snapshot ist identisch mit dem Vorimport — nur ein "
            "Vergleich wird ausgewiesen."
        )
    return {
        "asof": asof,
        "prev": prev,
        "prev_n": counts.get(prev, 0) if prev else 0,
        "prev_note": prev_note.strip(),
        "month": month,
        "month_n": counts.get(month, 0) if month else 0,
        "month_offset_days": offset,
        "month_note": month_note.strip(),
        "same_as_prev": bool(same),
        "target_date": target,
    }


def normalize_snapshot_frame(df: pd.DataFrame | None) -> pd.DataFrame | None:
    """Archiv-Frame aufbereiten: JSON-Listen dekodieren, bool-Spalten,
    eindeutige ``uid`` als str. ``None`` bleibt ``None``."""
    if df is None or df.empty:
        return None
    out = df.copy()
    if "uid" not in out.columns:
        out["uid"] = out["ticker"].astype(str)
    out["uid"] = out["uid"].astype(str)
    out = out.drop_duplicates("uid")
    if "filter_reasons" in out.columns:

        def _decode(v):
            if isinstance(v, list):
                return v
            if v is None or (isinstance(v, float) and pd.isna(v)):
                return []
            try:
                parsed = json.loads(v) if isinstance(v, str) else v
            except (TypeError, ValueError):
                return []
            return parsed if isinstance(parsed, list) else []

        out["filter_reasons"] = out["filter_reasons"].map(_decode)
    for col in ("filter_pass", "trend_warning", "is_financial", "is_real_estate"):
        if col in out.columns:
            out[col] = _bool(out, col)
    return out


def _load_snapshot(snapshot_date: date | None) -> pd.DataFrame | None:
    if snapshot_date is None:
        return None
    try:
        return normalize_snapshot_frame(
            persistence.load_universe_snapshot(snapshot_date)
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapshot %s nicht ladbar: %s", snapshot_date, exc)
        return None


# ── Universum & Veränderungen ──────────────────────────────────────────────


def _zone_counts(df: pd.DataFrame, cols: dict) -> dict[str, int]:
    zone = _zone_series(df, cols)
    counts = zone.value_counts()
    return {z: int(counts.get(z, 0)) for z in cols["zone_values"]}


def _class_counts(df: pd.DataFrame, cols: dict) -> dict[str, int]:
    klass = _text(df, cols["klass"]).map(
        lambda v: str(v).split(" ")[0] if v is not None else None
    )
    counts = klass.value_counts()
    order = ["A", "B+", "B", "C", "D", "F"]
    out = {k: int(counts.get(k, 0)) for k in order}
    rest = int(counts.sum()) - sum(out.values())
    if rest:
        out["–"] = rest
    return out


def _stock_columns(df: pd.DataFrame, cols: dict) -> list[str]:
    base = [
        "uid",
        "ticker",
        "name",
        "sector",
        "industry",
        "region",
        "market_cap",
        "last_price",
        cols["score"],
        cols["klass"],
        cols["zone"],
    ]
    if cols["z"]:
        base += [cols["z"], cols["pct"]]
    base += [f"z_{f}" for f in V2_FACTOR_NAMES]
    base += [f"cov_{f}" for f in V2_FACTOR_NAMES]
    base += [
        "data_coverage_v2",
        "data_coverage",
        "filter_pass",
        "filter_reasons",
        "trend_warning",
        "sma_signal",
        "trend_phase",
        "piotroski",
        "altman_z",
        "ret_1m",
        "ret_3m",
        "ret_6m",
        "ret_12m",
        "mom_12_1",
        "volatility_1y",
        "beta",
        "total_score",
        "classification",
        "recommendation",
    ]
    seen: set[str] = set()
    out: list[str] = []
    for c in base:
        if c in df.columns and c not in seen:
            out.append(c)
            seen.add(c)
    return out


def compute_universe_overview(
    df: pd.DataFrame, cols: dict, *, top_n: int, portfolio_uids: set[str]
) -> dict:
    n = int(len(df))
    eligible = _is_eligible(df, cols)
    zone = _zone_series(df, cols)
    score = _num(df, cols["score"])
    sma = _text(df, "sma_signal").map(lambda v: str(v) if v is not None else "")
    n_golden = int(sma.str.contains("GOLDEN", na=False).sum())
    n_death = int(sma.str.contains("DEATH", na=False).sum())
    n_above_200 = int(
        sma.str.contains("GOLDEN", na=False).sum()
        + sma.str.contains("Kurs > SMA-200", na=False).sum()
    )
    trend_warning = int(_bool(df, "trend_warning").sum()) if "trend_warning" in df.columns else n_death

    frame = df.copy()
    frame["in_portfolio"] = frame["uid"].astype(str).isin(portfolio_uids) if "uid" in frame.columns else False
    stock_cols = _stock_columns(frame, cols) + ["in_portfolio", "delta_prev", "delta_month", "zone_prev", "zone_month"]
    for extra in ("delta_prev", "delta_month", "zone_prev", "zone_month"):
        if extra not in frame.columns:
            frame[extra] = np.nan if extra.startswith("delta") else None

    cand_mask = zone.isin(cols["good_zones"])
    sort_col = cols["z"] or cols["score"]
    candidates = frame[cand_mask].sort_values(sort_col, ascending=False, na_position="last")
    sell = frame[zone.isin(cols["bad_zones"])].sort_values(sort_col, ascending=True, na_position="last")

    sector_rows: list[dict] = []
    if "sector" in frame.columns:
        grp = frame.assign(_zone=zone, _score=score, _elig=eligible).groupby(
            frame["sector"].fillna("Unbekannt").astype(str)
        )
        for sector, g in grp:
            n_s = int(len(g))
            n_c = int(g["_zone"].isin(cols["good_zones"]).sum())
            sector_rows.append(
                {
                    "sector": sector,
                    "n": n_s,
                    "n_eligible": int(g["_elig"].sum()),
                    "n_candidates": n_c,
                    "share_candidates": n_c / n_s if n_s else None,
                    "n_sell": int(g["_zone"].isin(cols["bad_zones"]).sum()),
                    "avg_score": _mean(g["_score"]),
                }
            )
        sector_rows.sort(key=lambda r: (-(r["avg_score"] or -1), r["sector"]))

    region_counts = (
        frame["region"].fillna("Unbekannt").astype(str).value_counts().to_dict()
        if "region" in frame.columns
        else {}
    )

    return {
        "available": True,
        "note": "",
        "n": n,
        "n_eligible": int(eligible.sum()),
        "share_eligible": float(eligible.sum()) / n if n else None,
        "zone_counts": _zone_counts(df, cols),
        "class_counts": _class_counts(df, cols),
        "avg_score": _mean(score),
        "median_score": _median(score),
        "avg_coverage": _mean(df["data_coverage_v2"]) if "data_coverage_v2" in df.columns else _mean(_num(df, "data_coverage")),
        "n_golden": n_golden,
        "n_death": n_death,
        "n_trend_warning": trend_warning,
        "breadth_above_sma200": n_above_200 / n if n else None,
        "top_candidates": _records(candidates.head(int(top_n)), stock_cols),
        "n_candidates_total": int(cand_mask.sum()),
        "all_candidates": _records(candidates, stock_cols),
        "sell_zone": _records(sell, stock_cols),
        "all_stocks": _records(frame.sort_values(sort_col, ascending=False, na_position="last"), stock_cols),
        "stock_columns": stock_cols,
        "sector_candidates": sector_rows,
        "region_counts": {str(k): int(v) for k, v in region_counts.items()},
    }


def compute_stock_deltas(
    current: pd.DataFrame, previous: pd.DataFrame, cols: dict
) -> pd.DataFrame:
    """Je Titel: Score/Zone/Klasse heute vs. Snapshot (outer-Merge auf uid).

    Spalten: ``uid, ticker, name, sector, score_now, score_prev, delta_score,
    zone_now, zone_prev, class_now, class_prev, dz_<faktor>, status``.
    """
    z_cols = [f"z_{f}" for f in V2_FACTOR_NAMES]

    def _slim(df: pd.DataFrame, suffix: str) -> pd.DataFrame:
        out = pd.DataFrame({"uid": df["uid"].astype(str)})
        for c in ("ticker", "name", "sector"):
            out[f"{c}_{suffix}"] = _text(df, c).values
        out[f"score_{suffix}"] = _num(df, cols["score"]).values
        out[f"zone_{suffix}"] = _zone_series(df, cols).values
        out[f"class_{suffix}"] = _text(df, cols["klass"]).map(
            lambda v: str(v).split(" ")[0] if v is not None else None
        ).values
        out[f"pct_{suffix}"] = _num(df, cols["pct"]).values if cols["pct"] else np.nan
        out[f"filter_pass_{suffix}"] = _is_eligible(df, cols).values
        if suffix == "now":
            out["filter_reasons"] = (
                df["filter_reasons"].map(_list_join).values
                if "filter_reasons" in df.columns
                else ""
            )
        for z in z_cols:
            out[f"{z}_{suffix}"] = _num(df, z).values
        return out.drop_duplicates("uid")

    cur = _slim(current, "now")
    prev = _slim(previous, "prev")
    merged = cur.merge(prev, on="uid", how="outer", indicator=True)
    status = merged["_merge"].map(
        {"both": STATUS_BOTH, "left_only": STATUS_NEW, "right_only": STATUS_GONE}
    )
    out = pd.DataFrame({"uid": merged["uid"], "status": status.astype(str)})
    for c in ("ticker", "name", "sector"):
        out[c] = merged[f"{c}_now"].where(merged[f"{c}_now"].notna(), merged[f"{c}_prev"])
    for c in ("score", "zone", "class", "pct", "filter_pass"):
        out[f"{c}_now"] = merged[f"{c}_now"]
        out[f"{c}_prev"] = merged[f"{c}_prev"]
    out["filter_reasons"] = merged["filter_reasons"].fillna("")
    out["delta_score"] = out["score_now"] - out["score_prev"]
    out["delta_pct"] = out["pct_now"] - out["pct_prev"]
    for z in z_cols:
        out[f"d{z}"] = merged[f"{z}_now"] - merged[f"{z}_prev"]
    out["zone_changed"] = (
        (out["status"] == STATUS_BOTH)
        & out["zone_now"].notna()
        & out["zone_prev"].notna()
        & (out["zone_now"].astype(str) != out["zone_prev"].astype(str))
    )
    out = out.drop(columns=[c for c in out.columns if c.startswith("pct_")])
    return out.reset_index(drop=True)


def zone_transition_matrix(
    deltas: pd.DataFrame, zones: tuple[str, ...] = ZONES
) -> pd.DataFrame:
    """Kreuztabelle Zone vorher (Zeilen) × Zone heute (Spalten), feste
    Reihenfolge; zusätzlich Zeile „neu" (nicht im Vorsnapshot) und Spalte
    „weg" (nicht mehr im Universum)."""
    rows = list(zones) + ["neu"]
    columns = list(zones) + ["weg"]
    if deltas is None or deltas.empty:
        return pd.DataFrame(0, index=rows, columns=columns, dtype=int)
    prev = deltas["zone_prev"].astype(object).where(deltas["zone_prev"].notna(), "neu")
    now = deltas["zone_now"].astype(object).where(deltas["zone_now"].notna(), "weg")
    prev = prev.where(deltas["status"] != STATUS_NEW, "neu")
    now = now.where(deltas["status"] != STATUS_GONE, "weg")
    ct = pd.crosstab(prev.astype(str), now.astype(str))
    return ct.reindex(index=rows, columns=columns, fill_value=0).astype(int)


def _spearman(a: pd.Series, b: pd.Series) -> float | None:
    both = pd.concat([a, b], axis=1).dropna()
    if len(both) < 5:
        return None
    rho = both.iloc[:, 0].rank().corr(both.iloc[:, 1].rank())
    return _float_or_none(rho)


def _changes_block(
    current: pd.DataFrame,
    snapshot: pd.DataFrame | None,
    snapshot_date: date | None,
    cols: dict,
    note: str,
) -> dict:
    if snapshot is None or snapshot_date is None:
        return _unavailable(note or "Kein Vergleichs-Snapshot verfügbar.")
    if cols["score"] not in snapshot.columns or not _num(snapshot, cols["score"]).notna().any():
        return _unavailable(
            f"Snapshot vom {snapshot_date.isoformat()} enthält keine Werte für "
            f"'{cols['score']}' — Vergleich nicht möglich."
        )
    deltas = compute_stock_deltas(current, snapshot, cols)
    both = deltas[deltas["status"] == STATUS_BOTH]
    movers_base = both[both["filter_pass_now"].astype(bool) & both["delta_score"].notna()]
    movers_up = movers_base[movers_base["delta_score"] >= MIN_ABS_DELTA_MOVER].sort_values(
        "delta_score", ascending=False
    ).head(TOP_MOVERS)
    movers_down = movers_base[movers_base["delta_score"] <= -MIN_ABS_DELTA_MOVER].sort_values(
        "delta_score", ascending=True
    ).head(TOP_MOVERS)

    good, bad, filt = cols["good_zones"], cols["bad_zones"], cols["filter_zones"]
    zone_now = both["zone_now"].astype(str)
    zone_prev = both["zone_prev"].astype(str)
    new_cand = both[zone_now.isin(good) & ~zone_prev.isin(good)]
    new_cand = pd.concat(
        [new_cand, deltas[(deltas["status"] == STATUS_NEW) & deltas["zone_now"].isin(good)]]
    ).drop_duplicates("uid")
    dropped_cand = both[~zone_now.isin(good) & zone_prev.isin(good)]
    gone_cand = deltas[(deltas["status"] == STATUS_GONE) & deltas["zone_prev"].isin(good)]
    new_sell = both[zone_now.isin(bad) & ~zone_prev.isin(bad) & ~zone_prev.isin(filt)]
    new_filter = both[zone_now.isin(filt) & ~zone_prev.isin(filt)]
    recovered = both[~zone_now.isin(filt) & zone_prev.isin(filt)]

    delta_cols = [
        "uid", "ticker", "name", "sector", "status", "score_prev", "score_now",
        "delta_score", "zone_prev", "zone_now", "class_prev", "class_now",
        "filter_reasons",
    ] + [f"dz_{f}" for f in V2_FACTOR_NAMES]

    rho = _spearman(both["score_now"], both["score_prev"])
    n_both = int(len(both))
    n_changed = int(both["zone_changed"].sum())
    zone_counts_prev = {
        z: int((snapshot[cols["zone"]].astype(str) == z).sum()) if cols["zone"] in snapshot.columns else 0
        for z in cols["zone_values"]
    }
    return {
        "available": True,
        "note": note,
        "snapshot_date": snapshot_date,
        "n_snapshot": int(len(snapshot)),
        "n_both": n_both,
        "n_new": int((deltas["status"] == STATUS_NEW).sum()),
        "n_gone": int((deltas["status"] == STATUS_GONE).sum()),
        "new_in_universe": _records(deltas[deltas["status"] == STATUS_NEW], delta_cols),
        "gone_from_universe": _records(deltas[deltas["status"] == STATUS_GONE], delta_cols),
        "movers_up": _records(movers_up, delta_cols),
        "movers_down": _records(movers_down, delta_cols),
        "new_candidates": _records(new_cand.sort_values("score_now", ascending=False), delta_cols),
        "dropped_candidates": _records(dropped_cand.sort_values("score_now", ascending=True), delta_cols),
        "gone_candidates": _records(gone_cand, delta_cols),
        "new_sell": _records(new_sell.sort_values("score_now", ascending=True), delta_cols),
        "new_filter_fail": _records(new_filter, delta_cols),
        "recovered_from_filter": _records(recovered, delta_cols),
        "zone_matrix": zone_transition_matrix(deltas, cols["zone_values"]),
        "zone_counts_prev": zone_counts_prev,
        "spearman_rho": rho,
        "zone_turnover": (n_changed / n_both) if n_both else None,
        "n_zone_changed": n_changed,
        "avg_abs_delta": _mean(both["delta_score"].abs()),
        "deltas": deltas,
        "delta_columns": delta_cols,
    }


# ── Sektoren ───────────────────────────────────────────────────────────────


def rrg_quadrant(mom_12_1, sma200_dist) -> str:
    """Rotations-Quadrant (wie ``sektor_momentum._rrg_card``): Momentum
    12M−1M (x) × Distanz zu SMA-200 (y)."""
    mom = _float_or_none(mom_12_1)
    sma = _float_or_none(sma200_dist)
    if mom is None or sma is None:
        return RRG_NONE
    if mom > 0 and sma > 0:
        return RRG_LEADING
    if mom > 0 and sma <= 0:
        return RRG_WEAKENING
    if mom <= 0 and sma > 0:
        return RRG_IMPROVING
    return RRG_LAGGING


def _sector_means(df: pd.DataFrame | None, cols: dict) -> dict[str, float]:
    if df is None or df.empty or "sector" not in df.columns or cols["score"] not in df.columns:
        return {}
    s = _num(df, cols["score"])
    return (
        s.groupby(df["sector"].fillna("Unbekannt").astype(str)).mean().dropna().to_dict()
    )


def compute_sector_section(
    df: pd.DataFrame,
    cols: dict,
    prev: pd.DataFrame | None,
    month: pd.DataFrame | None,
    comparison: dict,
    *,
    portfolio_sector_weights: dict[str, float] | None,
    benchmark_sector_weights: dict[str, float] | None,
) -> dict:
    from app.core.sector_momentum import aggregate_sectors

    if "sector" not in df.columns:
        return _unavailable("Keine Sektorspalte im Universum.")
    try:
        history = persistence.load_sector_score_history()
    except Exception as exc:  # noqa: BLE001
        log.warning("Sektor-Historie nicht ladbar: %s", exc)
        history = None
    agg = aggregate_sectors(df, history=history, score_col=cols["score"])
    if not agg:
        return _unavailable("Keine Sektoraggregate berechenbar.")

    prev_means = _sector_means(prev, cols)
    month_means = _sector_means(month, cols)

    # Quadrant ~1M aus der persistierten Sektor-Historie (mom_12_1/sma200_dist).
    rrg_prev: dict[str, str] = {}
    if history is not None and not history.empty and comparison.get("month"):
        level = agg[0].get("history_level", "sector")
        hist = history[(history["level"] == level)]
        target = comparison["month"]
        if not hist.empty:
            hist = hist.assign(_dist=(pd.to_datetime(hist["snapshot_date"]) - pd.Timestamp(target)).abs())
            nearest = hist.sort_values("_dist").drop_duplicates("key")
            for _, r in nearest.iterrows():
                if r["_dist"].days <= MONTH_TOLERANCE_DAYS:
                    rrg_prev[str(r["key"])] = rrg_quadrant(r.get("mom_12_1"), r.get("sma200_dist"))

    zone = _zone_series(df, cols)
    sectors = df["sector"].fillna("Unbekannt").astype(str)
    n_cand = zone.isin(cols["good_zones"]).groupby(sectors).sum()
    n_sell = zone.isin(cols["bad_zones"]).groupby(sectors).sum()

    rows: list[dict] = []
    for s in agg:
        name = s["sector"]
        score = _float_or_none(s.get("score"))
        p = prev_means.get(name)
        m = month_means.get(name)
        quad = rrg_quadrant(s.get("mom_12_1"), s.get("sma200_dist"))
        pf_w = (portfolio_sector_weights or {}).get(name)
        bm_w = (benchmark_sector_weights or {}).get(name)
        rows.append(
            {
                "sector": name,
                "n": int(s.get("count") or 0),
                "score": score,
                "score_prev": p,
                "delta_prev": (score - p) if (score is not None and p is not None) else None,
                "score_month": m,
                "delta_month": (score - m) if (score is not None and m is not None) else None,
                "delta_history_1m": _float_or_none(s.get("delta_score")),
                "ret_1m": _float_or_none(s.get("ret_1m")),
                "ret_3m": _float_or_none(s.get("ret_3m")),
                "ret_6m": _float_or_none(s.get("ret_6m")),
                "ret_12m": _float_or_none(s.get("ret_12m")),
                "mom_12_1": _float_or_none(s.get("mom_12_1")),
                "sma50_dist": _float_or_none(s.get("sma50_dist")),
                "sma200_dist": _float_or_none(s.get("sma200_dist")),
                "breadth_sma200": _float_or_none(s.get("breadth_sma200")),
                "breadth_golden": _float_or_none(s.get("breadth_golden")),
                "rrg": quad,
                "rrg_prev": rrg_prev.get(name, RRG_NONE),
                "rrg_changed": bool(rrg_prev.get(name) and rrg_prev.get(name) != quad),
                "n_candidates": int(n_cand.get(name, 0)),
                "share_candidates": (int(n_cand.get(name, 0)) / int(s.get("count") or 1)),
                "n_sell": int(n_sell.get(name, 0)),
                "pf_weight": pf_w,
                "bm_weight": bm_w,
                "active_weight": (pf_w or 0.0) - (bm_w or 0.0) if (pf_w is not None or bm_w is not None) else None,
                "low_confidence": bool(s.get("low_confidence")),
                "confidence_reasons": list(s.get("confidence_reasons") or []),
                "spark": list(s.get("spark") or []),
                "industries": [
                    {
                        "industry": i.get("industry"),
                        "n": int(i.get("count") or 0),
                        "score": _float_or_none(i.get("score")),
                        "delta_score": _float_or_none(i.get("delta_score")),
                        "mom_12_1": _float_or_none(i.get("mom_12_1")),
                        "sma200_dist": _float_or_none(i.get("sma200_dist")),
                        "breadth_sma200": _float_or_none(i.get("breadth_sma200")),
                    }
                    for i in (s.get("industries") or [])
                ],
            }
        )
    rows.sort(key=lambda r: (-(r["score"] if r["score"] is not None else -1e9), r["sector"]))
    quadrant_counts = {q: 0 for q in (RRG_LEADING, RRG_IMPROVING, RRG_WEAKENING, RRG_LAGGING, RRG_NONE)}
    for r in rows:
        quadrant_counts[r["rrg"]] = quadrant_counts.get(r["rrg"], 0) + 1
    quadrants = {
        q: [r["sector"] for r in rows if r["rrg"] == q]
        for q in (RRG_LEADING, RRG_IMPROVING, RRG_WEAKENING, RRG_LAGGING)
    }
    return {
        "available": True,
        "note": "",
        "rows": rows,
        "quadrant_counts": quadrant_counts,
        "quadrants": quadrants,
        "has_prev": bool(prev_means),
        "has_month": bool(month_means),
        "history_level": agg[0].get("history_level", "sector"),
    }


# ── Exposure-Helfer (Portfolio & Modellportfolio) ──────────────────────────


def _benchmark(settings: Settings, uni: pd.DataFrame, asof: date):
    from app.core.portfolio_construction import load_benchmark_weights

    regions = sorted(uni.get("region", pd.Series(dtype=str)).dropna().astype(str).unique())
    return load_benchmark_weights(settings, universe_regions=regions, asof=asof, universe=uni)


def _exposure_table(
    weights: pd.Series,
    groups: pd.Series,
    benchmark: dict[str, float] | None,
    band: float,
) -> list[dict]:
    agg = weights.groupby(groups.fillna("Unbekannt").astype(str)).sum()
    names = sorted(set(agg.index) | set((benchmark or {}).keys()))
    rows = []
    for name in names:
        w = float(agg.get(name, 0.0))
        b = float((benchmark or {}).get(name, 0.0)) if benchmark is not None else None
        active = (w - b) if b is not None else None
        rows.append(
            {
                "name": name,
                "portfolio": w,
                "benchmark": b,
                "active": active,
                "band_breach": bool(active is not None and abs(active) > band + 1e-12),
            }
        )
    rows.sort(key=lambda r: -r["portfolio"])
    return rows


def _factor_exposure(
    weights: pd.Series, uni: pd.DataFrame, cols: dict
) -> list[dict]:
    rows = []
    cand = uni[_zone_series(uni, cols).isin(cols["good_zones"])]
    for f in V2_FACTOR_NAMES:
        col = f"z_{f}"
        if col not in uni.columns:
            continue
        z = _num(uni, col)
        held = z.reindex(weights.index)
        w = weights.reindex(held.index).fillna(0.0)
        valid = held.notna()
        weighted = float((held[valid] * w[valid]).sum() / w[valid].sum()) if w[valid].sum() > 0 else None
        rows.append(
            {
                "factor": f,
                "pf_weighted": weighted,
                "pf_mean": _mean(held),
                "universe_mean": _mean(z),
                "candidates_mean": _mean(_num(cand, col)),
            }
        )
    return rows


def _concentration(weights: pd.Series, cap: float) -> dict:
    w = weights.dropna().astype(float)
    w = w[w > 0].sort_values(ascending=False)
    hhi = float((w ** 2).sum()) if not w.empty else None
    return {
        "n_positions": int(len(w)),
        "top5_weight": float(w.head(5).sum()) if not w.empty else None,
        "top10_weight": float(w.head(10).sum()) if not w.empty else None,
        "max_weight": float(w.max()) if not w.empty else None,
        "hhi": hhi,
        "effective_n": (1.0 / hhi) if hhi else None,
        "n_above_cap": int((w > cap + 1e-12).sum()),
        "cap": cap,
    }


# ── Aktives Portfolio ──────────────────────────────────────────────────────


def _delta_lookup(changes: dict, key: str) -> dict:
    block = changes.get(key) or {}
    deltas = block.get("deltas") if block.get("available") else None
    if deltas is None or deltas.empty:
        return {}
    return deltas.set_index("uid")[["delta_score", "zone_prev", "score_prev"]].to_dict("index")


def compute_portfolio_section(
    state,
    df: pd.DataFrame,
    cols: dict,
    settings: Settings,
    portfolio_id: int | None,
    asof: date,
    changes: dict,
    agent_ratings: pd.DataFrame | None,
    diagnostics: list[Diagnostic],
) -> dict:
    from app.core.signal_events import load_signal_events

    resolved = state.resolve_portfolio(portfolio_id)
    name = state.portfolio_name(portfolio_id) or (
        state.active_portfolio_name or "Standard-Portfolio (nicht gespeichert)"
    )
    if resolved is None or resolved.empty:
        return _unavailable("Kein Portfolio hochgeladen — bitte auf /portfolios eine Watchlist importieren.")

    weights = state.portfolio_weights(portfolio_id)
    ok = resolved[resolved["status"] == "ok"]
    ok_uids = set(ok["uid"].astype(str))
    missing = resolved.loc[resolved["status"] == "missing", "ticker"].astype(str).tolist()
    ambiguous = resolved.loc[resolved["status"] == "ambiguous", "ticker"].astype(str).tolist()
    has_weights = bool(state.ms_portfolio_weights) if portfolio_id in (None, state.active_portfolio_id) else (
        "weight" in resolved.columns and pd.to_numeric(resolved["weight"], errors="coerce").notna().any()
    )

    uni = _uid_index(df)
    view = uni[uni["uid"].isin(ok_uids)].copy()
    try:
        events = load_signal_events(df)
    except Exception as exc:  # noqa: BLE001
        log.warning("Signal-Events nicht ladbar: %s", exc)
        events = pd.DataFrame()
    if not events.empty:
        ev_key = "uid" if "uid" in events.columns else "ticker"
        ev = events.drop_duplicates(ev_key).set_index(events[ev_key].astype(str))
        for col in ("is_new", "state_since", "days_in_state", "momentum_prev"):
            if col in ev.columns:
                view[col] = view["uid"].map(ev[col])
    if "is_new" not in view.columns:
        view["is_new"] = False
    view["is_new"] = _bool(view, "is_new")
    view["weight"] = view["uid"].map(weights).astype(float)

    if agent_ratings is not None and not agent_ratings.empty and "ticker" in view.columns:
        ar = agent_ratings.drop_duplicates("ticker").set_index(agent_ratings["ticker"].astype(str))
        view["agent_rating"] = view["ticker"].astype(str).map(ar["rating"])
        view["agent_rated_at"] = view["ticker"].astype(str).map(ar["created_at"])
    else:
        view["agent_rating"] = None
        view["agent_rated_at"] = None

    flagged = build_flags(view)
    flag_map = flagged.set_index("uid")["flags"].to_dict() if not flagged.empty else {}
    sev_map = flagged.set_index("uid")["severity"].to_dict() if not flagged.empty else {}
    view["flags"] = view["uid"].map(lambda u: list(flag_map.get(u, [])))
    view["severity"] = view["uid"].map(sev_map)

    for key, suffix in (("vs_prev", "prev"), ("vs_month", "month")):
        lookup = _delta_lookup(changes, key)
        view[f"delta_{suffix}"] = view["uid"].map(lambda u: (lookup.get(u) or {}).get("delta_score"))
        view[f"zone_{suffix}"] = view["uid"].map(lambda u: (lookup.get(u) or {}).get("zone_prev"))
        view[f"score_{suffix}"] = view["uid"].map(lambda u: (lookup.get(u) or {}).get("score_prev"))

    score = _num(view, cols["score"])
    w = view["weight"].fillna(0.0)
    zone = _zone_series(view, cols)

    def _wavg(values: pd.Series) -> float | None:
        valid = values.notna() & (w > 0)
        return float((values[valid] * w[valid]).sum() / w[valid].sum()) if w[valid].sum() > 0 else None

    uni_score = _num(df, cols["score"])
    zone_weights = {z: float(w[zone == z].sum()) for z in cols["zone_values"]}
    zone_counts = {z: int((zone == z).sum()) for z in cols["zone_values"]}

    # Konzentration & Exposures.
    conc = _concentration(view.set_index("uid")["weight"], float(settings.pc_weight_cap))
    bm = _benchmark(settings, uni, asof)
    diagnostics.extend(bm.diagnostics)
    w_idx = view.set_index("uid")["weight"].fillna(0.0)
    sector_exp = _exposure_table(w_idx, view.set_index("uid").get("sector", pd.Series(dtype=object)), bm.sector, float(settings.pc_sector_band)) if "sector" in view.columns else []
    region_exp = _exposure_table(w_idx, view.set_index("uid").get("region", pd.Series(dtype=object)), bm.region, float(settings.pc_region_band)) if "region" in view.columns else []
    factor_exp = _factor_exposure(w_idx, uni, cols)

    # Sofortmaßnahmen, Signalwechsel, Watchlist.
    immediate = view[zone.isin(cols["filter_zones"]) | zone.isin(cols["bad_zones"])].copy()
    immediate["reason"] = immediate.apply(
        lambda r: (
            "Filter: " + _list_join(r.get("filter_reasons"))
            if str(r.get(cols["zone"])) in cols["filter_zones"] and _list_join(r.get("filter_reasons"))
            else f"{cols['zone_label']} {r.get(cols['zone'])}"
        ),
        axis=1,
    ) if not immediate.empty else pd.Series(dtype=object)

    momentum_now = _text(view, "sma_signal")
    signal_changes = view[view["is_new"]].copy()
    if not signal_changes.empty:
        signal_changes["momentum_now"] = momentum_now[view["is_new"]]

    watch_rows = pd.DataFrame()
    if cols["pct"] and cols["pct"] in view.columns:
        pct = _num(view, cols["pct"])
        exit_pct = float(settings.pc_exit_pct)
        near_exit = view[(pct >= exit_pct) & (pct <= exit_pct + WATCHLIST_BAND) & ~zone.isin(cols["filter_zones"])].copy()
        if not near_exit.empty:
            near_exit["watch_reason"] = "verkaufsgefährdet"
            watch_rows = near_exit

    position_cols = [
        "uid", "ticker", "name", "sector", "region", "weight", "market_cap",
        cols["score"], cols["klass"], cols["zone"],
    ] + ([cols["z"], cols["pct"]] if cols["z"] else []) + [
        f"z_{f}" for f in V2_FACTOR_NAMES
    ] + [f"cov_{f}" for f in V2_FACTOR_NAMES] + [
        "data_coverage_v2", "filter_pass", "filter_reasons", "trend_warning",
        "sma_signal", "trend_phase", "is_new", "state_since", "days_in_state",
        "ret_1m", "ret_3m", "ret_12m", "piotroski", "altman_z", "agent_rating",
        "agent_rated_at", "flags", "severity", "delta_prev", "zone_prev",
        "delta_month", "zone_month", "total_score", "classification",
        "recommendation",
    ]
    position_cols = [c for c in dict.fromkeys(position_cols) if c in view.columns]
    positions = view.sort_values("weight", ascending=False, na_position="last")

    flags_table = view[view["flags"].map(len) > 0].sort_values(
        ["severity", cols["score"]], ascending=[True, True], na_position="last"
    )

    wavg_prev = _wavg(_num(view, "score_prev"))
    wavg_month = _wavg(_num(view, "score_month"))
    wavg = _wavg(score)
    zone_changes = view[
        view["zone_prev"].notna() & (view["zone_prev"].astype(str) != zone.astype(str))
    ]

    return {
        "available": True,
        "note": "",
        "portfolio_id": portfolio_id,
        "name": name,
        "n_positions": int(len(resolved)),
        "n_resolved": int(len(ok)),
        "missing": missing,
        "ambiguous": ambiguous,
        "has_weights": bool(has_weights),
        "weight_resolved": float(w.sum()),
        "weighted_avg_score": wavg,
        "equal_avg_score": _mean(score),
        "weighted_avg_score_prev": wavg_prev,
        "weighted_avg_score_month": wavg_month,
        "delta_weighted_prev": (wavg - wavg_prev) if (wavg is not None and wavg_prev is not None) else None,
        "delta_weighted_month": (wavg - wavg_month) if (wavg is not None and wavg_month is not None) else None,
        "universe_avg_score": _mean(uni_score),
        "universe_median_score": _median(uni_score),
        "zone_weights": zone_weights,
        "zone_counts": zone_counts,
        "class_counts": _class_counts(view, cols),
        "n_flagged": int((view["flags"].map(len) > 0).sum()),
        "n_filter_fail": int(zone.isin(cols["filter_zones"]).sum()),
        "n_sell_zone": int(zone.isin(cols["bad_zones"]).sum()),
        "n_trend_warning": int(_bool(view, "trend_warning").sum()) if "trend_warning" in view.columns else 0,
        "n_signal_new": int(view["is_new"].sum()),
        "concentration": conc,
        "sector_exposure": sector_exp,
        "region_exposure": region_exp,
        "sector_weights": {r["name"]: r["portfolio"] for r in sector_exp},
        "benchmark_sector_weights": bm.sector,
        "benchmark_region_weights": bm.region,
        "factor_exposure": factor_exp,
        "positions": _records(positions, position_cols),
        "position_columns": position_cols,
        "flags_table": _records(flags_table, position_cols),
        "immediate_actions": _records(immediate, ["uid", "ticker", "name", "sector", "weight", cols["score"], cols["zone"], "reason"]),
        "signal_changes": _records(signal_changes, ["uid", "ticker", "name", "sector", "weight", "momentum_prev", "momentum_now", "state_since", cols["score"], cols["zone"]]),
        "zone_changes": _records(zone_changes, ["uid", "ticker", "name", "weight", "zone_prev", cols["zone"], "score_prev", cols["score"], "delta_prev"]),
        "watchlist_exit": _records(watch_rows, ["uid", "ticker", "name", "weight", cols["score"], cols["pct"], cols["zone"], "watch_reason"]) if not watch_rows.empty else [],
        "sector_band": float(settings.pc_sector_band),
        "region_band": float(settings.pc_region_band),
    }


def compute_watchlist_entry(df: pd.DataFrame, cols: dict, settings: Settings) -> list[dict]:
    """HALTEN-Titel knapp unter der Einstiegsschwelle („Kandidaten in spe")."""
    if not cols["pct"] or cols["pct"] not in df.columns:
        return []
    pct = _num(df, cols["pct"])
    zone = _zone_series(df, cols)
    entry = float(settings.pc_entry_pct)
    mask = zone.isin(cols["hold_zones"]) & (pct >= entry - WATCHLIST_BAND) & (pct < entry)
    rows = df[mask].sort_values(cols["pct"], ascending=False)
    return _records(rows, ["uid", "ticker", "name", "sector", cols["score"], cols["pct"], cols["zone"], "sma_signal"])


def compute_candidate_signal_changes(df: pd.DataFrame, cols: dict) -> list[dict]:
    """Signalwechsel (Golden/Death Cross) bei Kandidaten seit dem Vorimport."""
    from app.core.signal_events import load_signal_events

    try:
        events = load_signal_events(df)
    except Exception as exc:  # noqa: BLE001
        log.warning("Signal-Events nicht ladbar: %s", exc)
        return []
    if events.empty:
        return []
    key = "uid" if "uid" in df.columns else "ticker"
    ev_key = "uid" if "uid" in events.columns else "ticker"
    new_ev = events[_bool(events, "is_new") & events["momentum"].isin([MOMENTUM_GOLDEN, MOMENTUM_DEATH])]
    if new_ev.empty:
        return []
    zone = _zone_series(df, cols)
    cand = df[zone.isin(cols["good_zones"])]
    merged = cand.merge(
        new_ev[[ev_key, "momentum", "momentum_prev", "state_since"]],
        left_on=key, right_on=ev_key, how="inner", suffixes=("", "_ev"),
    )
    return _records(merged, ["uid", "ticker", "name", "sector", cols["score"], cols["zone"], "momentum_prev", "momentum", "state_since"])


# ── Modellportfolio ────────────────────────────────────────────────────────


def compute_model_portfolio_section(
    state,
    df: pd.DataFrame,
    cols: dict,
    settings: Settings,
    asof: date,
    today: date,
    diagnostics: list[Diagnostic],
) -> dict:
    from app.core.diagnostics import diags_from_json
    from app.core.pc_profiles import resolve_construction_settings
    from app.core.portfolio_construction import (
        ACTION_BUY,
        ACTION_DEFERRED,
        ACTION_HOLD,
        ACTION_INCREASE,
        ACTION_REDUCE,
        ACTION_SELL,
        build_trade_list,
    )

    try:
        mp = persistence.load_model_portfolio()
        meta = persistence.load_model_portfolio_meta()
    except Exception as exc:  # noqa: BLE001
        log.warning("Modellportfolio nicht ladbar: %s", exc)
        mp, meta = None, None
    if mp is None or mp.empty or meta is None:
        return _unavailable(
            "Kein gespeichertes Modellportfolio — auf /modellportfolio einen Lauf "
            "speichern oder `python -m app.tools.model_portfolio build` ausführen."
        )

    run_date = meta.get("snapshot_date")
    uni = _uid_index(df)
    mpf = mp.copy()
    mpf["uid"] = mpf["uid"].astype(str)
    for col in ("ticker", "name", "sector", "region", "trend_warning", cols["score"], cols["zone"], cols["klass"]):
        if col in uni.columns:
            mpf[f"{col}_now" if col in (cols["score"], cols["zone"], cols["klass"]) else col] = mpf["uid"].map(uni[col])
    mpf["zone_at_run"] = mpf["zone_v2"]
    mpf["composite_z_at_run"] = mpf["composite_z"]
    mpf["composite_pct_at_run"] = mpf["composite_pct"]
    zone_now_col = f"{cols['zone']}_now"
    if zone_now_col in mpf.columns:
        mpf["zone_drift"] = mpf["zone_at_run"].astype(str) != mpf[zone_now_col].astype(str)
    else:
        mpf["zone_drift"] = False
    mpf["in_universe"] = mpf["uid"].isin(uni.index)

    # Bestand: Quellportfolio des Laufs, sonst aktuelle Auswahl der Seite.
    source_pid = meta.get("source_portfolio_id")
    known = {int(p["id"]) for p in getattr(state, "ms_portfolios", []) if "id" in p}
    if source_pid is not None and known and int(source_pid) not in known:
        diagnostics.append(
            Diagnostic(
                SEV_WARNING,
                "report_model_source_missing",
                f"Bestandsportfolio des Modellportfolio-Laufs (#{source_pid}) existiert "
                "nicht mehr — Trade-Liste gegen die aktuelle Auswahl gerechnet",
            )
        )
        source_pid = state.model_source_portfolio_id()
    elif source_pid is None:
        source_pid = state.model_source_portfolio_id()
    current = state.portfolio_weights(int(source_pid) if source_pid is not None else None)
    pc_settings, profile = resolve_construction_settings(settings, int(source_pid) if source_pid is not None else None)

    target = mpf.set_index("uid")[["weight_effective", "composite_z", "zone_v2", "reason"]].copy()
    if "trend_warning" in mpf.columns:
        target["trend_warning"] = mpf.set_index("uid")["trend_warning"]
    mode = str(meta.get("rebalance_mode") or "full")
    try:
        trade_list = build_trade_list(target, current, pc_settings, mode, universe=uni)
        trades = trade_list.trades.copy()
        turnover = float(trade_list.turnover_oneway)
        n_deferred = int(trade_list.n_deferred)
        diagnostics.extend(trade_list.diagnostics)
    except Exception as exc:  # noqa: BLE001
        log.warning("Trade-Liste nicht rekonstruierbar: %s", exc)
        trades = pd.DataFrame()
        turnover, n_deferred = float(meta.get("turnover_oneway") or 0.0), int(meta.get("n_deferred") or 0)
        diagnostics.append(
            Diagnostic(SEV_WARNING, "report_trade_list_failed", f"Trade-Liste nicht rekonstruierbar: {exc}")
        )

    saved_action = mpf.set_index("uid")["action"].to_dict()
    if not trades.empty:
        trades["uid"] = trades["uid"].astype(str)
        for col in ("ticker", "name", "sector"):
            if col in uni.columns:
                trades[col] = trades["uid"].map(uni[col])
        trades["action_saved"] = trades["uid"].map(saved_action)
        changed = trades[trades["action_saved"].notna() & (trades["action_saved"] != trades["action"])]
        if not changed.empty:
            diagnostics.append(
                Diagnostic(
                    SEV_INFO,
                    "report_trade_list_drift",
                    f"{len(changed)} Trade-Aktion(en) weichen vom gespeicherten Lauf ab — "
                    "Bestand oder Turnover-Budget seit dem Lauf verändert",
                )
            )
        order = {ACTION_SELL: 0, ACTION_BUY: 1, ACTION_REDUCE: 2, ACTION_INCREASE: 3, ACTION_DEFERRED: 4, ACTION_HOLD: 5}
        trades["_ord"] = trades["action"].map(order).fillna(9)
        trades["_abs"] = trades["delta_w"].abs()
        trades = trades.sort_values(["_ord", "_abs"], ascending=[True, False]).drop(columns=["_ord", "_abs"])
    active_trades = trades[trades["action"] != ACTION_HOLD] if not trades.empty else trades
    counts = active_trades["action"].value_counts().to_dict() if not active_trades.empty else {}

    w_idx = mpf.set_index("uid")["weight_effective"].astype(float).fillna(0.0)
    bm = _benchmark(settings, uni, asof)
    sector_exp = _exposure_table(w_idx, mpf.set_index("uid").get("sector", pd.Series(dtype=object)), bm.sector, float(pc_settings.pc_sector_band)) if "sector" in mpf.columns else []
    region_exp = _exposure_table(w_idx, mpf.set_index("uid").get("region", pd.Series(dtype=object)), bm.region, float(pc_settings.pc_region_band)) if "region" in mpf.columns else []
    factor_exp = _factor_exposure(w_idx, uni, cols)

    # Aktive Gewichte Modell − Bestand je Sektor.
    cur_series = pd.Series(current, dtype=float)
    cur_sectors = cur_series.index.to_series().map(uni["sector"]) if ("sector" in uni.columns and not cur_series.empty) else pd.Series(dtype=object)
    cur_by_sector = cur_series.groupby(cur_sectors.fillna("Unbekannt").astype(str)).sum().to_dict() if not cur_series.empty else {}
    vs_active = [
        {"name": r["name"], "model": r["portfolio"], "current": float(cur_by_sector.get(r["name"], 0.0)), "delta": r["portfolio"] - float(cur_by_sector.get(r["name"], 0.0))}
        for r in sector_exp
    ]

    try:
        overrides = persistence.load_overrides("active")
    except Exception:  # noqa: BLE001
        overrides = None
    override_rows: list[dict] = []
    if overrides is not None and not overrides.empty:
        ov = overrides.copy()
        ov["expires_at"] = pd.to_datetime(ov["expires_at"]).dt.date
        ov["days_left"] = ov["expires_at"].map(lambda d: (d - today).days)
        ov["due_soon"] = ov["days_left"] <= OVERRIDE_DUE_DAYS
        if "ticker" in uni.columns:
            ov["ticker"] = ov["uid"].astype(str).map(uni["ticker"]).fillna(ov["uid"])
        override_rows = _records(ov.sort_values("expires_at"), ["id", "uid", "ticker", "direction", "target_weight", "reason", "owner", "expires_at", "days_left", "due_soon"])

    try:
        run_diags = diags_from_json(meta.get("diagnostics") or "[]")
    except Exception:  # noqa: BLE001
        run_diags = []
    settings_hash = persistence.settings_hash_v2(settings)
    age_days = (today - run_date).days if isinstance(run_date, date) else None

    mp_cols = [
        "uid", "ticker", "name", "sector", "region", "weight_model", "weight_effective",
        "cte", "composite_z_at_run", "composite_pct_at_run", "zone_at_run",
        f"{cols['score']}_now", zone_now_col, "zone_drift", "action", "reason",
        "trend_warning", "override_id", "in_universe",
    ]
    mp_cols = [c for c in mp_cols if c in mpf.columns]
    trade_cols = ["uid", "ticker", "name", "sector", "action", "action_saved", "reason", "weight_current", "weight_target", "delta_w", "composite_z", "zone_v2", "trend_warning", "weight_effective_after_budget"]
    trade_cols = [c for c in trade_cols if not trades.empty and c in trades.columns]

    return {
        "available": True,
        "note": "",
        "run_date": run_date,
        "age_days": age_days,
        "rebalance_mode": mode,
        "n_titles": int(meta.get("n_titles") or len(mpf)),
        "te_ex_ante": _float_or_none(meta.get("te_ex_ante")),
        "te_coverage": _float_or_none(meta.get("te_coverage")),
        "turnover_saved": _float_or_none(meta.get("turnover_oneway")),
        "turnover_oneway": turnover,
        "n_trades_saved": int(meta.get("n_trades") or 0),
        "n_deferred": n_deferred,
        "n_buy": int(counts.get(ACTION_BUY, 0)),
        "n_sell": int(counts.get(ACTION_SELL, 0)),
        "n_increase": int(counts.get(ACTION_INCREASE, 0)),
        "n_reduce": int(counts.get(ACTION_REDUCE, 0)),
        "n_active_trades": int(len(active_trades)),
        "source_portfolio_id": source_pid,
        "source_portfolio_name": meta.get("source_portfolio_name") or state.portfolio_name(source_pid) or "–",
        "pc_profile_name": meta.get("pc_profile_name") or (profile["name"] if profile else None),
        "settings_hash": str(meta.get("settings_hash") or ""),
        "settings_hash_current": settings_hash,
        "settings_hash_matches": str(meta.get("settings_hash") or "") == settings_hash,
        "neut_scheme_hash": meta.get("neut_scheme_hash"),
        "run_diagnostics": run_diags,
        "n_zone_drift": int(mpf["zone_drift"].sum()),
        "n_not_in_universe": int((~mpf["in_universe"]).sum()),
        "portfolio": _records(mpf.sort_values("weight_effective", ascending=False), mp_cols),
        "portfolio_columns": mp_cols,
        "trades": _records(active_trades, trade_cols),
        "all_trades": _records(trades, trade_cols),
        "trade_columns": trade_cols,
        "sector_exposure": sector_exp,
        "region_exposure": region_exp,
        "factor_exposure": factor_exp,
        "vs_active": vs_active,
        "overrides": override_rows,
        "n_overrides_due": int(sum(1 for r in override_rows if r.get("due_soon"))),
        "sector_band": float(pc_settings.pc_sector_band),
        "region_band": float(pc_settings.pc_region_band),
    }


# ── Optionale Abschnitte ───────────────────────────────────────────────────


def compute_factor_timing_section(df: pd.DataFrame, settings: Settings) -> dict:
    from app.core import factor_timing as ft

    try:
        history = persistence.load_factor_timing_history(limit=12)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(f"Factor-Timing-Historie nicht ladbar: {exc}")
    if not history:
        return _unavailable("Noch kein Factor-Timing-Regime persistiert (Seite /factor-timing einmal berechnen).")
    current = history[0]
    regime = str(current.get("regime") or "–")
    since = current["snapshot_date"]
    for h in history:
        if str(h.get("regime")) == regime:
            since = h["snapshot_date"]
        else:
            break
    strategic = ft.strategic_weights_from_settings(settings)
    tactical = current.get("weights") or {}
    rows = [
        {
            "factor": f,
            "strategic": float(strategic.get(f, 0.0)),
            "tactical": _float_or_none(tactical.get(f)),
            "tilt": (float(tactical[f]) - float(strategic.get(f, 0.0))) if tactical.get(f) is not None else None,
        }
        for f in ft.FACTORS
    ]
    try:
        inputs = persistence.load_factor_timing_inputs() or {}
    except Exception:  # noqa: BLE001
        inputs = {}
    try:
        momentum = ft.factor_momentum_from_universe(df)
    except Exception:  # noqa: BLE001
        momentum = {}
    try:
        spread = ft.value_spread(df)
    except Exception:  # noqa: BLE001
        spread = None
    return {
        "available": True,
        "note": "",
        "regime": regime,
        "regime_since": since,
        "regime_date": current["snapshot_date"],
        "mode": str(getattr(settings, "factor_timing_mode", "monitor")),
        "weights": rows,
        "inputs": {k: _float_or_none(v) for k, v in inputs.items()},
        "momentum_proxies": {k: _float_or_none(v) for k, v in momentum.items()},
        "value_spread": _float_or_none(spread),
        "timeline": [{"date": h["snapshot_date"], "regime": str(h.get("regime"))} for h in history],
    }


def compute_risk_section(
    state, df: pd.DataFrame, settings: Settings, portfolio_id: int | None, asof: date
) -> dict:
    from app.core import av_store

    try:
        meta = av_store.get_symbol_meta(settings.risk_benchmark_symbol)
    except Exception:  # noqa: BLE001
        meta = None
    if not meta:
        return _unavailable(
            f"Kein Kurscache für Benchmark {settings.risk_benchmark_symbol} — "
            "`python -m app.tools.risk_report update` ausführen."
        )
    resolved = state.resolve_portfolio(portfolio_id)
    if resolved is None or resolved.empty:
        return _unavailable("Kein Portfolio für die Risikoanalyse.")
    tickers = resolved["uid"].astype(str).tolist()
    weights = state.portfolio_weights(portfolio_id)
    try:
        from app.core.risk_report import compute_risk_report

        res = compute_risk_report(tickers, weights, settings, df, asof)
    except ValueError as exc:
        return _unavailable(f"Risikoanalyse nicht möglich: {exc}")
    except Exception as exc:  # noqa: BLE001
        log.exception("Risikoanalyse fehlgeschlagen")
        return _unavailable(f"Risikoanalyse fehlgeschlagen: {exc}")

    expost = {k: _float_or_none(v) if not isinstance(v, int) else v for k, v in (res.get("expost") or {}).items()}
    mcte = res.get("mcte")
    ranking = res.get("ranking")
    ranking_rows = _records(ranking.sort_values("cte", ascending=False).head(10), ["ticker", "gewicht", "mcte", "cte", "cte_bp", "composite_score", "zone_v2", "total_score", "recommendation", "sma_signal", "sector"]) if ranking is not None and not ranking.empty else []
    sektor = res.get("sektor_cte")
    sektor_rows = _records(sektor.sort_values("cte", ascending=False), ["sektor", "gewicht", "cte", "cte_bp"]) if sektor is not None and not sektor.empty else []
    szen = [
        {
            "name": s.name, "start": s.start, "ende": s.ende, "coverage": s.coverage,
            "belastbar": bool(s.belastbar), "pf_rendite": s.pf_rendite, "bm_rendite": s.bm_rendite,
            "aktiv": s.aktiv, "max_drawdown": s.max_drawdown,
        }
        for s in (res.get("szenarien") or [])
    ]
    schocks = res.get("schocks")
    schock_rows = _records(schocks, ["szenario", "pf_pnl", "bm_pnl", "aktiv", "abdeckung", "n_geringe_guete"]) if schocks is not None and not schocks.empty else []
    quality = res.get("quality")
    return {
        "available": True,
        "note": "",
        "benchmark": res.get("benchmark"),
        "variante": res.get("variante"),
        "expost": expost,
        "te_ex_ante": _float_or_none(getattr(mcte, "te_ledoit_wolf", None)) if mcte else None,
        "te_sample": _float_or_none(getattr(mcte, "te_sample", None)) if mcte else None,
        "mcte_error": res.get("mcte_fehler") or "",
        "ranking": ranking_rows,
        "sector_cte": sektor_rows,
        "scenarios": szen,
        "shocks": schock_rows,
        "shock_error": res.get("schock_fehler") or "",
        "unresolved": list(getattr(quality, "unresolved", []) or []),
        "missing_cache": list(getattr(quality, "missing_cache", []) or []),
    }


def compute_agents_section(
    df: pd.DataFrame, cols: dict, portfolio_uids: set[str], today: date,
    ratings: pd.DataFrame | None,
) -> dict:
    if ratings is None or ratings.empty:
        return _unavailable("Keine gespeicherten Agenten-Analysen.")
    uni = df.copy()
    if "ticker" not in uni.columns:
        return _unavailable("Universum ohne Ticker-Spalte.")
    r = ratings.copy()
    r["ticker"] = r["ticker"].astype(str)
    r["created_at"] = pd.to_datetime(r["created_at"], errors="coerce")
    r["age_days"] = r["created_at"].map(lambda t: (today - t.date()).days if pd.notna(t) else None)
    merged = r.merge(
        uni[[c for c in ("uid", "ticker", "name", "sector", cols["score"], cols["zone"]) if c in uni.columns]],
        on="ticker", how="left",
    )
    dup = merged["ticker"].duplicated(keep=False)
    merged["in_universe"] = merged["uid"].notna() if "uid" in merged.columns else False
    merged["in_portfolio"] = merged["uid"].astype(str).isin(portfolio_uids) if "uid" in merged.columns else False
    merged["tone"] = merged["rating"].map(lambda v: AGENT_RATING_TONE.get(str(v)))
    zone = _zone_series(merged, cols) if cols["zone"] in merged.columns else pd.Series([None] * len(merged))
    conflict = (
        ((merged["tone"] == "down") & zone.isin(cols["good_zones"]))
        | ((merged["tone"] == "up") & zone.isin(cols["bad_zones"]))
    )
    merged["conflict"] = conflict
    merged["stale"] = merged["age_days"].map(lambda d: d is not None and d > AGENT_STALE_DAYS)
    out_cols = ["uid", "ticker", "name", "sector", "rating", "tone", "created_at", "age_days", "stale", cols["score"], cols["zone"], "in_portfolio", "in_universe", "conflict"]
    merged = merged.sort_values("created_at", ascending=False)
    return {
        "available": True,
        "note": ("Ticker-Kollisionen bei: " + ", ".join(sorted(set(merged.loc[dup, "ticker"])))) if dup.any() else "",
        "rows": _records(merged, out_cols),
        "rating_counts": merged["rating"].value_counts().to_dict(),
        "n_stale": int(merged["stale"].sum()),
        "n_conflicts": int(conflict.sum()),
        "conflicts": _records(merged[conflict], out_cols),
        "portfolio_ratings": _records(merged[merged["in_portfolio"]], out_cols),
    }


# ── Datenqualität & Parameter ──────────────────────────────────────────────


def compute_data_quality_section(
    state,
    df: pd.DataFrame,
    cols: dict,
    settings: Settings,
    snapshots: list[tuple[date, int]],
    comparison: dict,
    prev: pd.DataFrame | None,
    month: pd.DataFrame | None,
    portfolio: dict,
    today: date,
) -> dict:
    from app.core.scoring_v2 import optional_column_available

    diags = list(getattr(state, "v2_diagnostics", []) or [])
    counts = count_by_severity(diags)
    missing_required = [c for c in KOYFIN_COLUMNS if c not in df.columns]
    optional = {c: bool(optional_column_available(df, c)) for c in OPTIONAL_COLUMNS}
    coverage = _num(df, "data_coverage_v2") if "data_coverage_v2" in df.columns else _num(df, "data_coverage")
    factor_cov = {f: _mean(_num(df, f"cov_{f}")) for f in V2_FACTOR_NAMES if f"cov_{f}" in df.columns}
    asof = comparison["asof"]
    age = (today - asof).days
    scheme_hash_now = settings.neut_scheme_hash() if hasattr(settings, "neut_scheme_hash") else ""
    scheme_drift = []
    for label, frame in (("Vorimport", prev), ("~1M", month)):
        if frame is not None and "neut_scheme_hash" in frame.columns:
            hashes = set(frame["neut_scheme_hash"].dropna().astype(str))
            if hashes and hashes != {scheme_hash_now}:
                scheme_drift.append(label)
    live_hash = set(df["neut_scheme_hash"].dropna().astype(str)) if "neut_scheme_hash" in df.columns else set()
    try:
        signal_hist = persistence.load_signal_history()
        n_signal_snaps = int(signal_hist["snapshot_date"].nunique()) if not signal_hist.empty else 0
    except Exception:  # noqa: BLE001
        n_signal_snaps = 0
    try:
        from app.core import av_store

        bm_meta = av_store.get_symbol_meta(settings.risk_benchmark_symbol)
    except Exception:  # noqa: BLE001
        bm_meta = None
    missing_fields = {
        "sector": int(df["sector"].isna().sum()) if "sector" in df.columns else len(df),
        "region": int(df["region"].isna().sum()) if "region" in df.columns else len(df),
        "market_cap": int(_num(df, "market_cap").isna().sum()),
    }
    return {
        "available": True,
        "note": "",
        "asof": asof,
        "import_age_days": age,
        "stale": age > STALE_IMPORT_DAYS,
        "n_universe": int(len(df)),
        "n_prev": comparison.get("prev_n"),
        "n_month": comparison.get("month_n"),
        "n_snapshots": len(snapshots),
        "oldest_snapshot": min((d for d, _ in snapshots), default=None),
        "diag_counts": counts,
        "diagnostics": sort_diagnostics(diags),
        "missing_required_columns": missing_required,
        "optional_columns": optional,
        "avg_coverage": _mean(coverage),
        "median_coverage": _median(coverage),
        "share_below_min_coverage": float((coverage < float(settings.filter_min_coverage)).sum()) / len(df) if len(df) and coverage.notna().any() else None,
        "factor_coverage": factor_cov,
        "duplicate_tickers": int(df["ticker"].astype(str).duplicated().sum()) if "ticker" in df.columns else 0,
        "missing_fields": missing_fields,
        "neut_scheme_hash": scheme_hash_now,
        "neut_scheme_drift": scheme_drift,
        "live_scheme_matches_settings": (not live_hash) or live_hash == {scheme_hash_now},
        "n_signal_snapshots": n_signal_snaps,
        "risk_cache": {
            "benchmark": settings.risk_benchmark_symbol,
            "available": bool(bm_meta),
            "last_refreshed": bm_meta.get("last_refreshed") if bm_meta else None,
        },
        "portfolio_missing": list(portfolio.get("missing", []) or []),
        "portfolio_ambiguous": list(portfolio.get("ambiguous", []) or []),
    }


def compute_parameter_appendix(settings: Settings, pc_profile: dict | None) -> dict:
    from app.core.pc_profiles import profile_differences

    def _group(names: list[str]) -> list[dict]:
        out = []
        for n in names:
            if not hasattr(settings, n):
                continue
            out.append(
                {
                    "key": n,
                    "label": SETTINGS_LABELS.get(n, n),
                    "value": getattr(settings, n),
                    "percent": n in PERCENT_SETTINGS,
                }
            )
        return out

    groups = [
        {"title": "Allgemein", "rows": _group(["scoring_version", "factor_timing_mode"])},
        {"title": "Faktorgewichte Composite v2", "rows": _group(["v2_weight_value", "v2_weight_quality", "v2_weight_momentum", "v2_weight_investment", "v2_min_factor_weight"])},
        {"title": "Composite-Parameter", "rows": _group(["v2_winsor_lower", "v2_winsor_upper", "v2_zscore_cap", "v2_composite_winsor_lower", "v2_composite_winsor_upper", "v2_min_group_size_l1", "v2_min_group_size_l2", "v2_min_group_valid", "v2_min_volatility"])},
        {"title": "Neutralisierung", "rows": _group(["v2_neut_scheme_default"]) + [
            {"key": f"neut_{k}", "label": f"Schema {k}", "value": v, "percent": False}
            for k, v in (settings.neut_scheme_map().items() if hasattr(settings, "neut_scheme_map") else [])
        ]},
        {"title": "Universumsfilter", "rows": _group(["filter_min_market_cap", "filter_min_piotroski", "filter_min_altman", "filter_min_adv", "filter_min_coverage", "filter_min_listing_days", "filter_max_de", "filter_min_icr"])},
        {"title": "Portfoliokonstruktion", "rows": _group(list(PC_PROFILE_FIELDS))},
        {"title": "Scoring v1 (Vergleichsmodus)", "rows": _group(["buy_threshold", "sell_threshold", "percentile_mode", "min_piotroski", "min_altman_z", "min_market_cap"]) + [
            {"key": f"v1_{k}", "label": f"Gewicht {k} (v1)", "value": v, "percent": True}
            for k, v in (getattr(settings, "factor_weights", {}) or {}).items()
        ]},
        {"title": "Risiko & Benchmark", "rows": _group(["risk_benchmark_symbol", "risk_report_dir"]) + [
            {"key": f"scenario_{k}", "label": f"Szenario {k}", "value": " – ".join(v) if isinstance(v, (list, tuple)) else v, "percent": False}
            for k, v in (getattr(settings, "risk_scenario_windows", {}) or {}).items()
        ]},
    ]
    profile_rows: list[dict] = []
    if pc_profile:
        for key, (base, new) in profile_differences(settings, pc_profile.get("data")).items():
            profile_rows.append({"key": key, "label": SETTINGS_LABELS.get(key, key), "global": base, "profile": new, "percent": key in PERCENT_SETTINGS})
    return {
        "available": True,
        "note": "",
        "settings_hash": persistence.settings_hash_v2(settings),
        "neut_scheme_hash": settings.neut_scheme_hash() if hasattr(settings, "neut_scheme_hash") else "",
        "groups": groups,
        "pc_profile_name": pc_profile["name"] if pc_profile else None,
        "profile_differences": profile_rows,
    }


# ── Key Findings ───────────────────────────────────────────────────────────


def _fmt_num(value, decimals: int = 1) -> str:
    from app.ui.formatters import fmt_de

    return fmt_de(value, decimals)


def _fmt_signed(value, decimals: int = 1) -> str:
    v = _float_or_none(value)
    if v is None:
        return "–"
    sign = "+" if v > 0 else ("−" if v < 0 else "±")
    return f"{sign}{_fmt_num(abs(v), decimals)}"


def _fmt_pct(value, decimals: int = 1) -> str:
    from app.ui.formatters import fmt_percent

    return fmt_percent(value, decimals)


def _fmt_date(value) -> str:
    if isinstance(value, (date, datetime)):
        return value.strftime("%d.%m.%Y")
    return str(value or "–")


def _tickers(rows: list[dict], n: int = 5) -> str:
    names = [str(r.get("ticker") or r.get("uid")) for r in rows[:n]]
    rest = len(rows) - len(names)
    return ", ".join(names) + (f" (+{rest})" if rest > 0 else "")


def generate_key_findings(report: dict) -> list[dict]:
    """Regelbasierte deutsche Kernaussagen (max. :data:`MAX_KEY_FINDINGS`)."""
    out: list[dict] = []

    def add(tone: str, text: str, section: str) -> None:
        out.append({"tone": tone, "text": text, "section": section})

    meta = report["meta"]
    cols = meta["cols"]
    pf = report.get("portfolio") or {}
    uni = report.get("universe") or {}
    changes = report.get("changes") or {}
    vs_prev = changes.get("vs_prev") or {}
    mp = report.get("model_portfolio") or {}
    sectors = report.get("sectors") or {}
    ft = report.get("factor_timing") or {}
    dq = report.get("data_quality") or {}
    risk = report.get("risk") or {}

    # 1 Portfolio-Handlungsbedarf
    if pf.get("available"):
        n_sell, n_filt = pf["n_sell_zone"], pf["n_filter_fail"]
        imm = pf.get("immediate_actions") or []
        if n_sell or n_filt:
            parts = []
            if n_filt:
                parts.append(f"{n_filt} in Zone FILTER ({_tickers([r for r in imm if str(r.get(cols['zone'])) in cols['filter_zones']])})")
            if n_sell:
                parts.append(f"{n_sell} in Zone {'/'.join(sorted(cols['bad_zones']))} ({_tickers([r for r in imm if str(r.get(cols['zone'])) in cols['bad_zones']])})")
            add("down", f"Sofortmaßnahmen im Portfolio „{pf['name']}“: " + "; ".join(parts) + ".", "portfolio")
        else:
            add("up", f"Keine Position des Portfolios „{pf['name']}“ in Zone FILTER oder {'/'.join(sorted(cols['bad_zones']))}.", "portfolio")
        if pf.get("n_flagged"):
            add("warn", f"{pf['n_flagged']} von {pf['n_resolved']} Positionen tragen mindestens ein Handlungs-Flag ({pf['n_trend_warning']} Death Cross, {pf['n_signal_new']} Signalwechsel seit Vorimport).", "portfolio")
        # 2 Portfolio-Score
        if pf.get("weighted_avg_score") is not None:
            txt = f"Gewichteter {cols['score_label']} des Portfolios {_fmt_num(pf['weighted_avg_score'])} (Universum Ø {_fmt_num(pf['universe_avg_score'])})"
            tone = "info"
            if pf.get("delta_weighted_prev") is not None:
                txt += f"; {_fmt_signed(pf['delta_weighted_prev'])} seit Vorimport ({_fmt_date(vs_prev.get('snapshot_date'))})"
                tone = "up" if pf["delta_weighted_prev"] > 0.5 else ("down" if pf["delta_weighted_prev"] < -0.5 else "info")
            if pf.get("delta_weighted_month") is not None and not meta["comparison"].get("same_as_prev"):
                txt += f", {_fmt_signed(pf['delta_weighted_month'])} seit ~1M"
            add(tone, txt + ".", "portfolio")
        # 3 Zonenwechsel im Portfolio
        for r in (pf.get("zone_changes") or [])[:3]:
            tone = "down" if str(r.get(cols["zone"])) in cols["bad_zones"] | cols["filter_zones"] else ("up" if str(r.get(cols["zone"])) in cols["good_zones"] else "warn")
            add(tone, f"{r.get('ticker')} von {r.get('zone_prev')} nach {r.get(cols['zone'])} gewechselt (Score {_fmt_num(r.get('score_prev'))} → {_fmt_num(r.get(cols['score']))}).", "portfolio")
        # 4 Bandverletzungen & Konzentration
        breaches = [r for r in (pf.get("sector_exposure") or []) if r.get("band_breach")]
        for r in breaches[:3]:
            add("warn", f"Sektor {r['name']}: aktives Gewicht {_fmt_signed((r['active'] or 0) * 100)} pp außerhalb des Bands ± {_fmt_num(pf['sector_band'] * 100, 0)} %.", "portfolio")
        conc = pf.get("concentration") or {}
        if conc.get("top10_weight") is not None:
            txt = f"Top-10-Gewicht {_fmt_pct(conc['top10_weight'])}, effektive Titelzahl {_fmt_num(conc.get('effective_n'), 1)}"
            if conc.get("n_above_cap"):
                txt += f"; {conc['n_above_cap']} Position(en) über Cap {_fmt_pct(conc['cap'], 0)}"
            add("warn" if conc.get("n_above_cap") else "info", txt + ".", "portfolio")
    else:
        add("warn", pf.get("note") or "Kein Portfolio verfügbar.", "portfolio")

    # 5 Modellportfolio
    if mp.get("available"):
        txt = (
            f"Modellportfolio vom {_fmt_date(mp['run_date'])} ({mp['rebalance_mode']}, {mp['n_titles']} Titel): "
            f"{mp['n_buy']} Käufe, {mp['n_sell']} Verkäufe, {mp['n_increase'] + mp['n_reduce']} Gewichtsanpassungen, "
            f"Turnover {_fmt_pct(mp['turnover_oneway'])}, TE ex-ante {_fmt_pct(mp['te_ex_ante'], 2)}"
        )
        if mp.get("n_deferred"):
            txt += f"; {mp['n_deferred']} Trade(s) verschoben"
        add("info", txt + ".", "model_portfolio")
        if not mp.get("settings_hash_matches"):
            add("warn", "Die Einstellungen haben sich seit dem Modellportfolio-Lauf geändert (Settings-Hash abweichend) — Lauf vor Entscheidungen erneuern.", "model_portfolio")
        elif mp.get("age_days") is not None and mp["age_days"] > 30:
            add("warn", f"Der Modellportfolio-Lauf ist {mp['age_days']} Tage alt.", "model_portfolio")
        if mp.get("n_zone_drift"):
            add("warn", f"{mp['n_zone_drift']} Zieltitel haben seit dem Lauf die Zone gewechselt.", "model_portfolio")
        if mp.get("n_overrides_due"):
            add("warn", f"{mp['n_overrides_due']} aktive(r) Override(s) laufen innerhalb von {OVERRIDE_DUE_DAYS} Tagen aus — erneuern oder schließen.", "model_portfolio")
    elif mp:
        add("info", mp.get("note") or "Kein Modellportfolio.", "model_portfolio")

    # 6 Universum / Zonen
    if uni.get("available"):
        n_kand = uni["zone_counts"].get(next(iter(sorted(cols["good_zones"]))), 0) if cols["version"] == "v2" else sum(uni["zone_counts"].get(z, 0) for z in cols["good_zones"])
        txt = f"{n_kand} Kandidaten von {uni['n']} Titeln ({uni['n_eligible']} eligible)"
        if vs_prev.get("available"):
            prev_kand = sum(vs_prev["zone_counts_prev"].get(z, 0) for z in cols["good_zones"])
            txt += f", {_fmt_signed(n_kand - prev_kand, 0)} vs. Vorimport; {len(vs_prev['new_candidates'])} neue Kandidaten"
            if vs_prev["new_candidates"]:
                txt += f" ({_tickers(vs_prev['new_candidates'])})"
            txt += f", {len(vs_prev['dropped_candidates'])} ausgeschieden"
        add("info", txt + ".", "universe")
    # 7 Ranking-Stabilität
    if vs_prev.get("available") and vs_prev.get("spearman_rho") is not None:
        rho, turn = vs_prev["spearman_rho"], vs_prev.get("zone_turnover") or 0.0
        tone = "warn" if (rho < RANK_STABILITY_WARN_RHO or turn > ZONE_TURNOVER_WARN) else "info"
        add(tone, f"Ranking-Stabilität zum Vorimport: Spearman ρ {_fmt_num(rho, 2)}, {_fmt_pct(turn)} der Titel wechselten die Zone.", "changes")
    # 8 Datenqualität — entscheidungsrelevante Warnungen vor den Sektoren
    if dq.get("available"):
        if dq.get("stale"):
            add("warn", f"Der Datenstand ist {dq['import_age_days']} Tage alt (Stand {_fmt_date(dq['asof'])}).", "data_quality")
        if dq.get("portfolio_missing"):
            add("warn", f"{len(dq['portfolio_missing'])} Portfolio-Ticker nicht im Universum: {', '.join(dq['portfolio_missing'][:5])}.", "data_quality")
        if dq.get("neut_scheme_drift"):
            add("warn", "Neutralisierungsschema seit dem Vergleichs-Snapshot geändert (" + ", ".join(dq["neut_scheme_drift"]) + ") — Deltas nur eingeschränkt vergleichbar.", "data_quality")
        c = dq.get("diag_counts") or {}
        if c.get(SEV_ERROR):
            add("down", f"{c[SEV_ERROR]} Diagnose-Fehler im aktuellen Scoring-Lauf — Details im Abschnitt Datenqualität.", "data_quality")
    # 9 Sektoren
    if sectors.get("available") and sectors["rows"]:
        top = sectors["rows"][0]
        txt = f"Stärkster Sektor {top['sector']} (Score {_fmt_num(top['score'])}, {top['rrg']})"
        with_delta = [r for r in sectors["rows"] if r.get("delta_month") is not None] or [r for r in sectors["rows"] if r.get("delta_prev") is not None]
        key = "delta_month" if any(r.get("delta_month") is not None for r in sectors["rows"]) else "delta_prev"
        if with_delta:
            best = max(with_delta, key=lambda r: r[key])
            worst = min(with_delta, key=lambda r: r[key])
            label = "1M" if key == "delta_month" else "Vorimport"
            txt += f"; größte Verbesserung seit {label}: {best['sector']} ({_fmt_signed(best[key])}), größter Rückgang: {worst['sector']} ({_fmt_signed(worst[key])})"
        changed = [r for r in sectors["rows"] if r.get("rrg_changed")]
        if changed:
            txt += "; Quadrantenwechsel: " + ", ".join(f"{r['sector']} {r['rrg_prev']} → {r['rrg']}" for r in changed[:3])
        add("info", txt + ".", "sectors")
    # 10 Regime
    if ft.get("available"):
        add("info", f"Factor-Timing-Regime {ft['regime']} seit {_fmt_date(ft['regime_since'])} (Modus {ft['mode']}).", "factor_timing")
    # 11 Risiko
    if risk.get("available"):
        ex = risk.get("expost") or {}
        add("info", f"Risiko ex-post: TE 1J {_fmt_pct(ex.get('te_1j'))}, aktive Rendite p. a. {_fmt_pct(ex.get('aktive_rendite_pa'))}, max. rel. Drawdown {_fmt_pct(ex.get('max_rel_drawdown'))}; TE ex-ante {_fmt_pct(risk.get('te_ex_ante'))}.", "risk")
    # 12 Datenqualität — Hinweis auf Warnungen zuletzt
    if dq.get("available"):
        c = dq.get("diag_counts") or {}
        if c.get(SEV_WARNING) and not c.get(SEV_ERROR):
            add("info", f"{c[SEV_WARNING]} Diagnose-Warnungen im aktuellen Scoring-Lauf.", "data_quality")

    return out[:MAX_KEY_FINDINGS]


# ── Dateiname & Orchestrierung ─────────────────────────────────────────────


def report_filename_stem(report: dict) -> str:
    import re

    name = str(report["meta"].get("portfolio_name") or "Universum")
    translit = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "Ae", "Ö": "Oe", "Ü": "Ue", "ß": "ss", "&": "und", " ": "_", "/": "_", "·": "_"})
    stem = f"{REPORT_TITLE}_{name}_{report['meta']['asof'].isoformat()}".translate(translit)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    return re.sub(r"_+", "_", stem).strip("_")


def build_committee_report(
    state, options: ReportOptions | None = None, *, today: date | None = None
) -> dict:
    """Orchestrator: baut das komplette Ergebnis-Dict (siehe Modul-Doku).

    Raises ``ValueError`` nur, wenn kein Universum geladen ist; alle anderen
    fehlenden Daten ergeben Abschnitte mit ``available = False``.
    """
    options = options or ReportOptions()
    today = today or date.today()
    settings: Settings = state.settings
    df: pd.DataFrame = state.scored
    if df is None or df.empty:
        raise ValueError("Kein Universum geladen — bitte zuerst einen Koyfin-Export importieren.")
    if "uid" not in df.columns:
        df = df.copy()
        df["uid"] = df["ticker"].astype(str)
    df = df.drop_duplicates("uid").reset_index(drop=True)

    diagnostics: list[Diagnostic] = []
    cols = resolve_score_columns(settings, df)
    asof = snapshot_date_from_universe(state.raw if state.raw is not None and not state.raw.empty else df)

    portfolio_id = options.portfolio_id if options.portfolio_id is not None else state.active_portfolio_id
    portfolio_name = state.portfolio_name(portfolio_id) or state.active_portfolio_name or "Standard-Portfolio"

    try:
        snapshots = persistence.list_snapshots()
    except Exception as exc:  # noqa: BLE001
        log.warning("Snapshot-Liste nicht ladbar: %s", exc)
        snapshots = []
    comparison = pick_comparison_snapshots(
        snapshots, asof, prev_override=options.prev_snapshot, month_override=options.month_snapshot
    )
    prev_frame = _load_snapshot(comparison["prev"])
    month_frame = None if comparison["same_as_prev"] else _load_snapshot(comparison["month"])
    if comparison["prev"] and prev_frame is None:
        comparison["prev_note"] = f"Snapshot {comparison['prev'].isoformat()} konnte nicht geladen werden."
    if comparison["month"] and not comparison["same_as_prev"] and month_frame is None:
        comparison["month_note"] = f"Snapshot {comparison['month'].isoformat()} konnte nicht geladen werden."

    changes = {
        "available": True,
        "note": "",
        "vs_prev": _changes_block(df, prev_frame, comparison["prev"], cols, comparison["prev_note"]),
        "vs_month": (
            _unavailable(comparison["month_note"])
            if comparison["same_as_prev"] or month_frame is None
            else _changes_block(df, month_frame, comparison["month"], cols, comparison["month_note"])
        ),
        "watchlist_entry": compute_watchlist_entry(df, cols, settings),
        "candidate_signal_changes": compute_candidate_signal_changes(df, cols),
    }
    if not changes["vs_prev"].get("available") and not changes["vs_month"].get("available"):
        changes["note"] = "Keine Vergleichs-Snapshots verfügbar — Veränderungen entfallen."

    # Δ-Spalten für Universum/Kandidaten-Tabellen.
    df_delta = df.copy()
    for key, suffix in (("vs_prev", "prev"), ("vs_month", "month")):
        lookup = _delta_lookup(changes, key)
        df_delta[f"delta_{suffix}"] = df_delta["uid"].astype(str).map(lambda u: (lookup.get(u) or {}).get("delta_score"))
        df_delta[f"zone_{suffix}"] = df_delta["uid"].astype(str).map(lambda u: (lookup.get(u) or {}).get("zone_prev"))

    try:
        agent_ratings = persistence.load_agent_ratings() if options.include_agents else None
    except Exception:  # noqa: BLE001
        agent_ratings = None

    portfolio = compute_portfolio_section(
        state, df, cols, settings, portfolio_id, asof, changes, agent_ratings, diagnostics
    )
    portfolio_uids = {str(r["uid"]) for r in portfolio.get("positions", [])} if portfolio.get("available") else set()
    universe = compute_universe_overview(df_delta, cols, top_n=int(options.top_n), portfolio_uids=portfolio_uids)

    sectors = (
        compute_sector_section(
            df, cols, prev_frame, month_frame, comparison,
            portfolio_sector_weights=portfolio.get("sector_weights") if portfolio.get("available") else None,
            benchmark_sector_weights=portfolio.get("benchmark_sector_weights") if portfolio.get("available") else None,
        )
        if options.include_sectors
        else _unavailable("Abschnitt abgewählt.")
    )
    model_portfolio = (
        compute_model_portfolio_section(state, df, cols, settings, asof, today, diagnostics)
        if options.include_model_portfolio
        else _unavailable("Abschnitt abgewählt.")
    )
    factor_timing = compute_factor_timing_section(df, settings) if options.include_factor_timing else _unavailable("Abschnitt abgewählt.")
    risk = compute_risk_section(state, df, settings, portfolio_id, asof) if options.include_risk else _unavailable("Abschnitt abgewählt (Opt-in).")
    agents = compute_agents_section(df, cols, portfolio_uids, today, agent_ratings) if options.include_agents else _unavailable("Abschnitt abgewählt.")
    data_quality = (
        compute_data_quality_section(state, df, cols, settings, snapshots, comparison, prev_frame, month_frame, portfolio, today)
        if options.include_diagnostics
        else _unavailable("Abschnitt abgewählt.")
    )
    pc_profile = None
    if options.include_parameters:
        try:
            from app.core.pc_profiles import resolve_construction_settings

            _, pc_profile = resolve_construction_settings(settings, portfolio_id)
        except Exception:  # noqa: BLE001
            pc_profile = None
    parameters = compute_parameter_appendix(settings, pc_profile) if options.include_parameters else _unavailable("Abschnitt abgewählt.")

    report = {
        "meta": {
            "title": REPORT_TITLE,
            "asof": asof,
            "generated_at": datetime.now(),
            "today": today,
            "prepared_by": options.prepared_by or "",
            "scoring_version": cols["version"],
            "cols": cols,
            "n_universe": int(len(df)),
            "portfolio_id": portfolio_id,
            "portfolio_name": portfolio_name,
            "comparison": comparison,
            "settings_hash": persistence.settings_hash_v2(settings),
            "neut_scheme_hash": settings.neut_scheme_hash() if hasattr(settings, "neut_scheme_hash") else "",
            "options": options,
        },
        "universe": universe,
        "changes": changes,
        "sectors": sectors,
        "portfolio": portfolio,
        "model_portfolio": model_portfolio,
        "factor_timing": factor_timing,
        "risk": risk,
        "agents": agents,
        "data_quality": data_quality,
        "parameters": parameters,
        "diagnostics": sort_diagnostics(diagnostics),
    }
    report["key_findings"] = generate_key_findings(report)
    report["meta"]["filename_stem"] = report_filename_stem(report)
    return report
