"""CSV-Parser, Snapshot-Builder und Universum-Aggregation fuer den
Sektor-Momentum-Screen."""

from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from .momentum import classify_momentum
from .sectors import display_name, group_for

log = logging.getLogger(__name__)


# ── Integritäts-Thresholds für die Sektor-/Industrie-Aggregation ────────────
# Modul-Konstanten (kein Settings-Feld), weil dies Engine-Verhalten ist und
# keine User-tunable Faktor-Gewichte. Konzeptuell getrennt von
# Settings.min_stocks_per_industry (Scoring-Vor-Filter): die folgenden Werte
# filtern Sektoren NICHT, sondern markieren sie als low_confidence.
MIN_TICKERS_PER_SECTOR: int = 3
MIN_TICKERS_PER_INDUSTRY: int = 3
HISTORY_TARGET_LAG_DAYS: int = 30
HISTORY_SNAPSHOT_TOLERANCE_DAYS: int = 15
HISTORY_STALENESS_DAYS: int = 7
HISTORY_MIN_SNAPSHOTS: int = 2


SECTOR_CSV_COLUMNS = [
    "ticker",
    "name",
    "last_price",
    "chg_1d_pct",
    "ret_5d_pct",
    "ret_1m_pct",
    "sma50_pct",
    "sma200_pct",
    "sma_50",
    "sma_200",
]

_NUMERIC_COLUMNS = [c for c in SECTOR_CSV_COLUMNS if c not in {"ticker", "name"}]


def load_sector_csv(source: str | bytes | io.StringIO) -> pd.DataFrame:
    """Parst einen Koyfin-Sektor-CSV-Export (10 Spalten).

    Erwartetes Format: Ticker, Name, Last Price, 1d Chg %, Total Return (5D),
    Total Return (1M), SMA % (50D), SMA % (200D), SMA (50D), SMA (200D).

    Unterstuetzt ``;`` und ``,`` als Trenner sowie deutsche Dezimalkommata.
    Zeilen mit Ticker ausserhalb der bekannten Sektor-/Industrie-Liste werden
    stillschweigend verworfen.
    """

    if isinstance(source, (bytes, bytearray)):
        raw = source.decode("utf-8", errors="replace")
    elif isinstance(source, io.StringIO):
        raw = source.getvalue()
    else:
        raw = Path(source).read_text(encoding="utf-8", errors="replace")

    sep = ";" if raw.count(";") > raw.count(",") else ","
    df = pd.read_csv(io.StringIO(raw), sep=sep, decimal=",", engine="python")

    df = df.iloc[:, : len(SECTOR_CSV_COLUMNS)].copy()
    df.columns = SECTOR_CSV_COLUMNS[: df.shape[1]]
    for col in SECTOR_CSV_COLUMNS:
        if col not in df.columns:
            df[col] = np.nan

    for col in _NUMERIC_COLUMNS:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=["ticker"]).copy()
    df["ticker"] = df["ticker"].astype(str).str.strip()
    df["group"] = df["ticker"].map(group_for)
    df = df.dropna(subset=["group"]).reset_index(drop=True)
    return df


def build_snapshot_frame(df: pd.DataFrame, snapshot_date: date) -> pd.DataFrame:
    """Erzeugt den persistierbaren Snapshot-DataFrame aus den Rohdaten."""

    if df.empty:
        return pd.DataFrame(
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

    out = df[["ticker", "group", "last_price", "sma_50", "sma_200"]].copy()
    out["display_name"] = out["ticker"].map(display_name)
    out["momentum"] = [
        classify_momentum(p, s50, s200)
        for p, s50, s200 in zip(out["last_price"], out["sma_50"], out["sma_200"])
    ]
    out.insert(0, "snapshot_date", pd.Timestamp(snapshot_date).date())
    return out[
        [
            "snapshot_date",
            "ticker",
            "group",
            "display_name",
            "last_price",
            "sma_50",
            "sma_200",
            "momentum",
        ]
    ]


# ── Universum-Aggregation für den Sektor-Momentum-Screen ────────────────────
#
# Aggregiert das gescorte Equity-Universum (``STATE.scored``) sektorweise und
# liefert pro Sektor: Score, ΔScore (gegenüber dem Vormonat), Returns, SMA-
# Distanzen, Momentum 12M-1M, Breadth-Anteile, 12-Wochen-Score-Sparkline
# (letzter Snapshot je Kalenderwoche) und Industrie-Sub-Aggregate. ΔScore und
# Sparkline werden aus persistierten Snapshots gespeist (siehe
# ``persistence.load_sector_score_history``); ohne Historie liefern sie
# ``NaN`` bzw. eine leere Liste — das UI zeigt dann „–".


def _mean_pct(series: pd.Series, robust: bool = False) -> float:
    """Mittelwert einer Dezimalanteil-Spalte als Prozent-Punkt-Wert.

    Sowohl Returns (``ret_1m`` …) als auch SMA-Distanzen (``sma_50_distance``,
    ``sma_200_distance``) liegen in ``STATE.scored`` als Dezimalanteile vor
    (0,12 = 12 %). Hier multiplizieren wir mit 100, damit das UI direkt mit
    ``%``-Werten arbeiten kann (Heatmap-Tone, Hero-Anzeige, RRG-Achsen etc.).

    ``robust=True`` schaltet auf den Median um (ausreißerrobust für kleine
    Sektoren); Default ``False`` belässt das arithmetische Mittel, damit
    bestehende Aufrufer unverändert weiterlaufen.
    """
    s = pd.to_numeric(series, errors="coerce").dropna()
    if s.empty:
        return float("nan")
    val = s.median() if robust else s.mean()
    return float(val * 100)




def _empty_history_meta() -> dict:
    return {
        "history_count": 0,
        "newest_snapshot_age_days": float("nan"),
        "prev_snapshot_offset_days": float("nan"),
        "prev_rejected_stale": False,
    }


def _history_lookup(
    history: pd.DataFrame | None,
    level: str,
    key: str,
    current_score: float,
    target_lag_days: int = HISTORY_TARGET_LAG_DAYS,
    tolerance_days: int = HISTORY_SNAPSHOT_TOLERANCE_DAYS,
    staleness_days: int = HISTORY_STALENESS_DAYS,
) -> tuple[float, list[float], dict]:
    """Liefert ``(prev_score, spark, meta)`` für einen Sektor/eine Industrie
    aus der persistierten Snapshot-Historie.

    ``prev_score`` ist der Score des Snapshots, der dem ~``target_lag_days``-Tage-
    Ziel vor dem jüngsten Snapshot am nächsten liegt – aber nur, wenn der Abstand
    zum Zielzeitpunkt innerhalb ``tolerance_days`` liegt. Ist der nächstgelegene
    Snapshot zu weit entfernt, wird ``prev_score`` zu ``NaN`` (kein irreführender
    ΔScore aus uralt-Daten) und ``meta["prev_rejected_stale"]`` ist ``True``.

    ``spark`` enthält bis zu 12 Score-Werte aufsteigend sortiert — je
    Kalenderwoche der letzte Snapshot, davon die 12 jüngsten Wochen. Damit ist
    die Auflösung unabhängig von der Upload-Frequenz: Bei mehreren Uploads pro
    Woche zählt nur der jeweils letzte, bei exakt wöchentlichen Uploads
    entspricht das den 12 letzten Snapshots. Der jüngste Wert wird durch
    ``current_score`` ersetzt, damit die Sparkline garantiert im Heute-Wert
    endet.

    ``meta`` liefert Diagnose-Felder für die Confidence-Markierung im Aufrufer:
    ``history_count`` (Anzahl Snapshots), ``newest_snapshot_age_days`` (Tage
    zwischen jüngstem Snapshot und Wall-Clock), ``prev_snapshot_offset_days``
    (Abstand des prev-Snapshots zum Zieldatum) und ``prev_rejected_stale``
    (Bool, ob prev wegen Toleranzüberschreitung verworfen wurde).

    Liefert ``(NaN, [], meta-mit-history_count=0)``, wenn keine Historie für
    ``(level, key)`` existiert.
    """
    meta = _empty_history_meta()
    if history is None or history.empty:
        return float("nan"), [], meta
    sub = history[(history["level"] == level) & (history["key"] == key)]
    if sub.empty:
        return float("nan"), [], meta
    sub = sub.sort_values("snapshot_date")
    meta["history_count"] = int(len(sub))
    valid = sub[pd.to_numeric(sub["score"], errors="coerce").notna()]
    if valid.empty:
        return float("nan"), [], meta

    # Wochen-Resampling: letzter Snapshot je ISO-Kalenderwoche, davon die 12
    # jüngsten Wochen. drop_duplicates(keep="last") auf der Wochen-Periode —
    # ``valid`` ist bereits aufsteigend nach Datum sortiert.
    week = pd.PeriodIndex(pd.to_datetime(valid["snapshot_date"]), freq="W")
    weekly = valid.assign(_week=week).drop_duplicates("_week", keep="last")
    spark = [
        round(float(v), 1)
        for v in pd.to_numeric(weekly["score"], errors="coerce").tolist()
    ][-12:]
    if pd.notna(current_score):
        spark[-1] = round(float(current_score), 1)

    latest = pd.Timestamp(sub["snapshot_date"].iloc[-1])
    today = pd.Timestamp.now().normalize()
    meta["newest_snapshot_age_days"] = float((today - latest) / pd.Timedelta(days=1))

    if len(sub) >= 2:
        target = latest - pd.Timedelta(days=target_lag_days)
        prev_rows = sub.iloc[:-1].copy()
        prev_rows["delta"] = (
            pd.to_datetime(prev_rows["snapshot_date"]) - target
        ).abs()
        best = prev_rows.sort_values("delta").iloc[0]
        offset_days = float(best["delta"] / pd.Timedelta(days=1))
        meta["prev_snapshot_offset_days"] = offset_days
        if offset_days > tolerance_days:
            prev = float("nan")
            meta["prev_rejected_stale"] = True
            log.warning(
                "sector_momentum: %s/%s prev-Snapshot %.0fd vom Ziel entfernt "
                "(>%dd) — ΔScore=NaN",
                level,
                key,
                offset_days,
                tolerance_days,
            )
        else:
            prev_val = pd.to_numeric(best["score"], errors="coerce")
            prev = float(prev_val) if pd.notna(prev_val) else float("nan")
    else:
        prev = float("nan")
        log.warning(
            "sector_momentum: %s/%s hat nur %d Snapshot(s) (<%d) — ΔScore=NaN",
            level,
            key,
            len(sub),
            HISTORY_MIN_SNAPSHOTS,
        )
    return prev, spark, meta


def _industry_aggregates(
    stocks: pd.DataFrame,
    history: pd.DataFrame | None = None,
    score_col: str = "total_score",
) -> list[dict]:
    """Industrie-Sub-Aggregate für eine Sektor-Gruppe."""
    if "industry" not in stocks.columns:
        return []
    if score_col not in stocks.columns:
        score_col = "total_score"
    level = "industry" if score_col == "total_score" else "industry_v2"
    out: list[dict] = []
    for industry, group in stocks.groupby("industry", dropna=True):
        if not industry:
            continue
        n = len(group)
        if n == 0:
            continue
        score_series = pd.to_numeric(group[score_col], errors="coerce").dropna()
        score = float(score_series.mean()) if not score_series.empty else float("nan")
        prev_score, _, _ = _history_lookup(history, level, str(industry), score)
        delta = (
            round(float(score - prev_score), 1)
            if pd.notna(score) and pd.notna(prev_score)
            else float("nan")
        )
        sma200 = _mean_pct(group.get("sma_200_distance", pd.Series(dtype=float)))
        sma50 = _mean_pct(group.get("sma_50_distance", pd.Series(dtype=float)))
        ret_1m = _mean_pct(group.get("ret_1m", pd.Series(dtype=float)))
        ret_12m = _mean_pct(group.get("ret_12m", pd.Series(dtype=float)))
        mom = (
            round(ret_12m - ret_1m, 1)
            if pd.notna(ret_12m) and pd.notna(ret_1m)
            else float("nan")
        )
        sma200_series = pd.to_numeric(
            group.get("sma_200_distance", pd.Series(dtype=float)), errors="coerce"
        )
        if not sma200_series.dropna().empty:
            breadth: float = round(
                float((sma200_series > 0).sum() / max(n, 1) * 100)
            )
        else:
            breadth = float("nan")

        ind_reasons: list[str] = []
        if n < MIN_TICKERS_PER_INDUSTRY:
            ind_reasons.append(f"count<{MIN_TICKERS_PER_INDUSTRY}")

        out.append(
            {
                "industry": str(industry),
                "count": int(n),
                "score": round(score, 1) if pd.notna(score) else float("nan"),
                "delta_score": delta,
                "mom_12_1": mom,
                "sma200_dist": round(sma200, 1) if pd.notna(sma200) else float("nan"),
                "sma50_dist": round(sma50, 1) if pd.notna(sma50) else float("nan"),
                "breadth_sma200": (
                    int(breadth) if pd.notna(breadth) else float("nan")
                ),
                "low_confidence": bool(ind_reasons),
                "confidence_reasons": ind_reasons,
                "history_level": level,
            }
        )
    out.sort(
        key=lambda d: (d["score"] if pd.notna(d["score"]) else -1e9),
        reverse=True,
    )
    return out


def aggregate_sectors(
    df: pd.DataFrame,
    history: pd.DataFrame | None = None,
    score_col: str = "total_score",
) -> list[dict]:
    """Aggregiert das Equity-Universum sektorweise für den Sektor-Momentum-Screen.

    ``df`` ist üblicherweise ``STATE.scored`` (nach :func:`compute_scores` und
    :func:`format_scored`, sodass ``sma_*_distance`` bereits in
    Prozent-Punkten vorliegt). Liefert pro Sektor ein Dict mit allen für den
    Screen benötigten Kennzahlen plus Industrie-Sub-Aggregate.

    ``history`` ist der DataFrame aus
    :func:`persistence.load_sector_score_history`. Enthält er Zeilen für
    einen Sektor (``level == "sector"``), wird ``delta_score`` als
    ``score_now − score_~1M`` und ``spark`` aus den letzten 12 Kalenderwochen
    (letzter Snapshot je Woche) berechnet. Ohne Historie sind beide Felder
    ``NaN`` bzw. ``[]``.
    """
    if df is None or df.empty or "sector" not in df.columns:
        return []
    if score_col not in df.columns:
        score_col = "total_score"

    # v2-Aggregate werden unter eigenen History-Levels geführt, damit sich
    # v1- und v2-Score-Deltas nie in einer Zeitreihe mischen.
    level = "sector" if score_col == "total_score" else "sector_v2"

    out: list[dict] = []
    for sector, group in df.groupby("sector", dropna=True):
        if not sector:
            continue
        n = len(group)
        if n == 0:
            continue

        score_series = pd.to_numeric(group[score_col], errors="coerce").dropna()
        score = float(score_series.mean()) if not score_series.empty else float("nan")
        prev_raw, spark, hist_meta = _history_lookup(
            history, level, str(sector), score
        )
        delta = (
            round(float(score - prev_raw), 1)
            if pd.notna(score) and pd.notna(prev_raw)
            else float("nan")
        )
        prev = round(prev_raw, 1) if pd.notna(prev_raw) else float("nan")

        ret_1m = _mean_pct(group.get("ret_1m", pd.Series(dtype=float)))
        ret_3m = _mean_pct(group.get("ret_3m", pd.Series(dtype=float)))
        ret_6m = _mean_pct(group.get("ret_6m", pd.Series(dtype=float)))
        ret_12m = _mean_pct(group.get("ret_12m", pd.Series(dtype=float)))
        mom_12_1 = (
            round(ret_12m - ret_1m, 1)
            if pd.notna(ret_12m) and pd.notna(ret_1m)
            else float("nan")
        )

        sma200 = _mean_pct(group.get("sma_200_distance", pd.Series(dtype=float)))
        sma50 = _mean_pct(group.get("sma_50_distance", pd.Series(dtype=float)))

        sma200_series = pd.to_numeric(
            group.get("sma_200_distance", pd.Series(dtype=float)), errors="coerce"
        )
        if not sma200_series.dropna().empty:
            breadth_sma200: float = round(
                float((sma200_series > 0).sum() / max(n, 1) * 100)
            )
        else:
            breadth_sma200 = float("nan")
        sma_signal = group.get("sma_signal", pd.Series(dtype=str)).astype(str)
        breadth_golden = round(
            float(sma_signal.str.contains("GOLDEN", na=False).sum() / max(n, 1) * 100)
        )

        reasons: list[str] = []
        if n < MIN_TICKERS_PER_SECTOR:
            reasons.append(f"count<{MIN_TICKERS_PER_SECTOR}")
            log.warning(
                "sector_momentum: Sektor %s hat nur %d Aktie(n) (<%d)",
                sector,
                n,
                MIN_TICKERS_PER_SECTOR,
            )
        if hist_meta["history_count"] < HISTORY_MIN_SNAPSHOTS:
            reasons.append("history<2")
        newest_age = hist_meta.get("newest_snapshot_age_days")
        if pd.notna(newest_age) and newest_age > HISTORY_STALENESS_DAYS:
            reasons.append(f"stale>{HISTORY_STALENESS_DAYS}d")
            log.warning(
                "sector_momentum: Sektor %s — newest Snapshot %.0f Tage alt (>%d)",
                sector,
                newest_age,
                HISTORY_STALENESS_DAYS,
            )
        if hist_meta.get("prev_rejected_stale"):
            reasons.append("prev_stale")

        out.append(
            {
                "sector": str(sector),
                "count": int(n),
                "score": round(score, 1) if pd.notna(score) else float("nan"),
                "prev_score": prev,
                "delta_score": delta,
                "ret_1m": round(ret_1m, 1) if pd.notna(ret_1m) else float("nan"),
                "ret_3m": round(ret_3m, 1) if pd.notna(ret_3m) else float("nan"),
                "ret_6m": round(ret_6m, 1) if pd.notna(ret_6m) else float("nan"),
                "ret_12m": round(ret_12m, 1) if pd.notna(ret_12m) else float("nan"),
                "mom_12_1": mom_12_1,
                "sma50_dist": round(sma50, 1) if pd.notna(sma50) else float("nan"),
                "sma200_dist": round(sma200, 1) if pd.notna(sma200) else float("nan"),
                "breadth_sma200": (
                    int(breadth_sma200) if pd.notna(breadth_sma200) else float("nan")
                ),
                "breadth_golden": int(breadth_golden),
                "spark": spark,
                "industries": _industry_aggregates(
                    group, history=history, score_col=score_col
                ),
                "low_confidence": bool(reasons),
                "confidence_reasons": reasons,
                "history_level": level,
            }
        )
    return out


def aggregates_to_history_records(agg: list[dict]) -> list[dict]:
    """Wandelt das ``aggregate_sectors``-Resultat in Zeilen für die
    ``sector_score_history``-Tabelle um (Sektor- und Industrie-Ebene)."""
    rows: list[dict] = []
    for s in agg:
        rows.append(
            {
                "level": s.get("history_level", "sector"),
                "key": s["sector"],
                "score": s.get("score"),
                "ret_1m": s.get("ret_1m"),
                "ret_12m": s.get("ret_12m"),
                "mom_12_1": s.get("mom_12_1"),
                "sma200_dist": s.get("sma200_dist"),
                "sma50_dist": s.get("sma50_dist"),
                "breadth_sma200": s.get("breadth_sma200"),
                "n": s.get("count"),
            }
        )
        for ind in s.get("industries", []) or []:
            rows.append(
                {
                    "level": ind.get("history_level", "industry"),
                    "key": ind["industry"],
                    "score": ind.get("score"),
                    "ret_1m": None,
                    "ret_12m": None,
                    "mom_12_1": ind.get("mom_12_1"),
                    "sma200_dist": ind.get("sma200_dist"),
                    "sma50_dist": ind.get("sma50_dist"),
                    "breadth_sma200": ind.get("breadth_sma200"),
                    "n": ind.get("count"),
                }
            )
    return rows
