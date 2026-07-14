"""Tests für die Universum-Aggregation und das Historie-Mapping in
``app.core.sector_momentum``.

Schwerpunkt: ΔScore und Sparkline werden aus persistierter Snapshot-Historie
gespeist; ohne Historie liefern sie ``NaN`` bzw. eine leere Liste.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
import pytest

from app.core import sector_momentum as sm
from app.core.sector_momentum import (
    HISTORY_STALENESS_DAYS,
    _mean_pct,
    aggregate_sectors,
    aggregates_to_history_records,
)


def _scored_frame() -> pd.DataFrame:
    """Mini-Universum mit zwei Sektoren à zwei Aktien.

    SMA-Distanzen und Returns liegen wie in ``STATE.scored`` als
    Dezimalanteile vor (0,12 = 12 %).
    """
    return pd.DataFrame(
        [
            {
                "ticker": "MSFT",
                "sector": "Technology",
                "industry": "Software",
                "total_score": 80.0,
                "ret_1m": 0.02,
                "ret_3m": 0.05,
                "ret_6m": 0.10,
                "ret_12m": 0.30,
                "sma_50_distance": 0.05,
                "sma_200_distance": 0.12,
                "sma_signal": "GOLDEN CROSS",
            },
            {
                "ticker": "ORCL",
                "sector": "Technology",
                "industry": "Software",
                "total_score": 70.0,
                "ret_1m": 0.01,
                "ret_3m": 0.04,
                "ret_6m": 0.08,
                "ret_12m": 0.20,
                "sma_50_distance": 0.03,
                "sma_200_distance": 0.08,
                "sma_signal": "UP",
            },
            {
                "ticker": "JPM",
                "sector": "Financials",
                "industry": "Banks",
                "total_score": 60.0,
                "ret_1m": 0.005,
                "ret_3m": 0.02,
                "ret_6m": 0.04,
                "ret_12m": 0.10,
                "sma_50_distance": 0.01,
                "sma_200_distance": 0.04,
                "sma_signal": "UP",
            },
            {
                "ticker": "GS",
                "sector": "Financials",
                "industry": "Banks",
                "total_score": 50.0,
                "ret_1m": -0.01,
                "ret_3m": 0.01,
                "ret_6m": 0.03,
                "ret_12m": 0.05,
                "sma_50_distance": -0.02,
                "sma_200_distance": -0.01,
                "sma_signal": "DOWN",
            },
        ]
    )


def _by_sector(agg: list[dict], name: str) -> dict:
    return next(d for d in agg if d["sector"] == name)


def test_aggregate_without_history_yields_nan_delta_and_empty_spark():
    df = _scored_frame()

    agg = aggregate_sectors(df)

    tech = _by_sector(agg, "Technology")
    assert pd.isna(tech["delta_score"])
    assert pd.isna(tech["prev_score"])
    assert tech["spark"] == []

    # Sektor-Mittel und Industrie-Aggregate bleiben echt berechnet.
    assert tech["score"] == 75.0
    assert tech["count"] == 2
    industries = tech["industries"]
    assert len(industries) == 1
    assert industries[0]["industry"] == "Software"
    assert pd.isna(industries[0]["delta_score"])


def test_aggregate_with_history_computes_delta_and_spark():
    df = _scored_frame()

    today = date(2025, 1, 1)
    history = pd.DataFrame(
        [
            # Vormonat-Snapshot (~30 Tage zurück) → Score 70 für Technology
            {
                "snapshot_date": today - timedelta(days=30),
                "level": "sector",
                "key": "Technology",
                "score": 70.0,
            },
            # Älterer Snapshot, soll als Spark-Punkt mitlaufen
            {
                "snapshot_date": today - timedelta(days=120),
                "level": "sector",
                "key": "Technology",
                "score": 65.0,
            },
            # Jüngster Snapshot trägt selbst keinen Score → wird im
            # Spark durch den Live-Score ersetzt.
            {
                "snapshot_date": today,
                "level": "sector",
                "key": "Technology",
                "score": 74.0,
            },
            # Industrie-Snapshot — Software lag bei 73
            {
                "snapshot_date": today - timedelta(days=30),
                "level": "industry",
                "key": "Software",
                "score": 73.0,
            },
            {
                "snapshot_date": today,
                "level": "industry",
                "key": "Software",
                "score": 74.5,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    tech = _by_sector(agg, "Technology")

    # ΔScore = score_now (75) − score_~1M (70) = 5
    assert tech["score"] == 75.0
    assert tech["prev_score"] == 70.0
    assert tech["delta_score"] == 5.0

    # Sparkline endet im Live-Score, enthält alle drei Historie-Punkte.
    assert tech["spark"][-1] == 75.0
    assert len(tech["spark"]) == 3
    assert tech["spark"][0] == 65.0  # ältester
    assert tech["spark"][1] == 70.0  # mittlerer

    # Industrie-Delta ebenfalls aus Historie: 75 − 73 = 2.0
    software = tech["industries"][0]
    assert software["industry"] == "Software"
    assert software["delta_score"] == 2.0


def test_aggregate_scales_sma_and_returns_to_percent_points():
    """SMA-Distanzen und Returns kommen aus ``STATE.scored`` als Dezimalanteile
    und müssen im Aggregat in Prozent-Punkten vorliegen — sonst zeigt das UI
    sie als ~0 % an (z. B. 0,2 % statt 10 %)."""
    df = _scored_frame()

    agg = aggregate_sectors(df)

    tech = _by_sector(agg, "Technology")
    # MSFT (12 %) + ORCL (8 %) → Ø 10 % SMA-200-Abstand
    assert tech["sma200_dist"] == 10.0
    # MSFT (5 %) + ORCL (3 %) → Ø 4 %
    assert tech["sma50_dist"] == 4.0
    # MSFT (2 %) + ORCL (1 %) → Ø 1,5 % 1M-Return
    assert tech["ret_1m"] == 1.5
    # 12M (30 % + 20 %)/2 = 25 %, 1M = 1,5 % → mom_12_1 = 23,5
    assert tech["mom_12_1"] == 23.5

    fin = _by_sector(agg, "Financials")
    # JPM (4 %) + GS (-1 %) → Ø 1,5 %
    assert fin["sma200_dist"] == 1.5


def test_aggregate_history_filtered_by_level():
    """Industrie-Snapshots dürfen nicht für Sektor-Lookups herangezogen werden."""
    df = _scored_frame()

    today = date(2025, 1, 1)
    history = pd.DataFrame(
        [
            {
                "snapshot_date": today - timedelta(days=30),
                "level": "industry",
                "key": "Technology",  # gleicher Name, aber Industrie-Level
                "score": 99.0,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    tech = _by_sector(agg, "Technology")
    assert pd.isna(tech["delta_score"])
    assert tech["spark"] == []


def test_aggregates_to_history_records_includes_sector_and_industries():
    df = _scored_frame()
    agg = aggregate_sectors(df)

    rows = aggregates_to_history_records(agg)

    levels = {(r["level"], r["key"]) for r in rows}
    assert ("sector", "Technology") in levels
    assert ("sector", "Financials") in levels
    assert ("industry", "Software") in levels
    assert ("industry", "Banks") in levels

    sector_row = next(
        r for r in rows if r["level"] == "sector" and r["key"] == "Technology"
    )
    assert sector_row["score"] == 75.0
    assert sector_row["n"] == 2


# ── Integritäts-Härtung: Confidence, Stale-History, NaN-Breadth ────────────


def _single_sector_frame() -> pd.DataFrame:
    """Universum mit einem 4-Stock-Sektor (≥ MIN_TICKERS_PER_SECTOR=3)."""
    return pd.DataFrame(
        [
            {
                "ticker": f"T{i}",
                "sector": "Energy",
                "industry": "Oil & Gas",
                "total_score": 60.0 + i,
                "ret_1m": 0.01,
                "ret_3m": 0.02,
                "ret_6m": 0.03,
                "ret_12m": 0.10,
                "sma_50_distance": 0.02,
                "sma_200_distance": 0.05,
                "sma_signal": "UP",
            }
            for i in range(4)
        ]
    )


def test_aggregate_low_confidence_when_below_threshold():
    """Sektoren mit weniger als MIN_TICKERS_PER_SECTOR Aktien werden markiert,
    aber nicht entfernt — Information loss wäre schlimmer als ein Badge."""
    df = _scored_frame()  # 2 Aktien je Sektor

    agg = aggregate_sectors(df)
    tech = _by_sector(agg, "Technology")

    assert tech["low_confidence"] is True
    assert "count<3" in tech["confidence_reasons"]
    # Sektor bleibt vorhanden (nicht herausgefiltert)
    assert tech["score"] == 75.0


def test_aggregate_history_rejects_stale_prev_snapshot():
    """Wenn der nächstgelegene prev-Snapshot zu weit vom 30-Tage-Ziel entfernt
    ist, wird ΔScore zu NaN und ``prev_stale`` ins confidence_reasons-Feld
    eingetragen — sonst suggeriert die UI einen Trend aus uralt-Daten."""
    df = _scored_frame()

    today = pd.Timestamp.now().normalize()
    history = pd.DataFrame(
        [
            # prev-Snapshot 90d zurück — offset zum 30d-Ziel = 60d, > tolerance (15d)
            {
                "snapshot_date": (today - pd.Timedelta(days=90)).date(),
                "level": "sector",
                "key": "Technology",
                "score": 70.0,
            },
            {
                "snapshot_date": today.date(),
                "level": "sector",
                "key": "Technology",
                "score": 74.0,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    tech = _by_sector(agg, "Technology")

    assert pd.isna(tech["delta_score"])
    assert pd.isna(tech["prev_score"])
    assert "prev_stale" in tech["confidence_reasons"]
    assert tech["low_confidence"] is True


def test_aggregate_history_accepts_within_tolerance():
    """Snapshot bei today-25d ist innerhalb ±15d des 30d-Ziels (|25-30|=5)
    und darf weiter für ΔScore verwendet werden."""
    df = _scored_frame()

    today = pd.Timestamp.now().normalize()
    history = pd.DataFrame(
        [
            {
                "snapshot_date": (today - pd.Timedelta(days=25)).date(),
                "level": "sector",
                "key": "Technology",
                "score": 70.0,
            },
            {
                "snapshot_date": today.date(),
                "level": "sector",
                "key": "Technology",
                "score": 74.0,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    tech = _by_sector(agg, "Technology")

    assert tech["prev_score"] == 70.0
    assert tech["delta_score"] == 5.0  # 75 − 70
    assert "prev_stale" not in tech["confidence_reasons"]


def test_aggregate_flags_stale_newest_snapshot():
    """Wenn der jüngste Snapshot älter als HISTORY_STALENESS_DAYS gegen
    Wall-Clock ist, signalisiert das Aggregat ``stale>Xd``."""
    df = _single_sector_frame()  # Energy mit 4 Tickern → kein count<3

    today = pd.Timestamp.now().normalize()
    # Newest Snapshot vor 14 Tagen (> 7d Schwelle)
    history = pd.DataFrame(
        [
            {
                "snapshot_date": (today - pd.Timedelta(days=44)).date(),
                "level": "sector",
                "key": "Energy",
                "score": 55.0,
            },
            {
                "snapshot_date": (today - pd.Timedelta(days=14)).date(),
                "level": "sector",
                "key": "Energy",
                "score": 60.0,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    energy = _by_sector(agg, "Energy")

    assert f"stale>{HISTORY_STALENESS_DAYS}d" in energy["confidence_reasons"]
    assert energy["low_confidence"] is True


def test_mean_pct_robust_uses_median():
    """``_mean_pct(robust=True)`` schaltet auf Median um — wichtig für kleine
    Sektoren mit Einzel-Ausreißern."""
    s = pd.Series([0.01, 0.01, 1.0])
    # Default Mean: (0.01 + 0.01 + 1.0)/3 * 100 = 34.0
    assert _mean_pct(s) == pytest.approx(34.0, abs=1e-6)
    # Robust Median: 0.01 * 100 = 1.0
    assert _mean_pct(s, robust=True) == pytest.approx(1.0, abs=1e-6)


def test_breadth_sma200_nan_when_all_missing():
    """Wenn alle ``sma_200_distance`` NaN sind, soll ``breadth_sma200`` NaN
    sein und NICHT still auf 0 zurückfallen (das wäre nicht von ‚0 % über
    SMA-200' unterscheidbar)."""
    df = _scored_frame()
    df["sma_200_distance"] = float("nan")

    agg = aggregate_sectors(df)
    tech = _by_sector(agg, "Technology")

    assert pd.isna(tech["breadth_sma200"])
    # Industrie-Aggregat ebenfalls
    assert pd.isna(tech["industries"][0]["breadth_sma200"])


def test_history_count_one_yields_low_confidence():
    """Genau 1 historischer Snapshot reicht nicht für ΔScore — und löst das
    ``history<2`` Reason aus."""
    df = _single_sector_frame()
    today = pd.Timestamp.now().normalize()
    history = pd.DataFrame(
        [
            {
                "snapshot_date": today.date(),
                "level": "sector",
                "key": "Energy",
                "score": 60.0,
            },
        ]
    )

    agg = aggregate_sectors(df, history=history)
    energy = _by_sector(agg, "Energy")

    assert pd.isna(energy["delta_score"])
    assert "history<2" in energy["confidence_reasons"]


def test_aggregate_logs_warning_on_short_history(caplog):
    """``_history_lookup`` warnt im Log, wenn die Historie zu kurz für
    ΔScore ist — damit Datenqualität nicht stillschweigend leidet."""
    df = _single_sector_frame()
    today = pd.Timestamp.now().normalize()
    history = pd.DataFrame(
        [
            {
                "snapshot_date": today.date(),
                "level": "sector",
                "key": "Energy",
                "score": 60.0,
            },
        ]
    )

    with caplog.at_level(logging.WARNING, logger=sm.__name__):
        aggregate_sectors(df, history=history)

    assert any(
        "nur 1 Snapshot" in rec.message or "<2" in rec.message
        for rec in caplog.records
    )
