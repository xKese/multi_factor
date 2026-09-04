"""Integrationstests der Scoring-v2-Design-Integration (WP6/WP7)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores
from app.core.scoring_v2 import compute_scores_v2
from app.core.sector_momentum import (
    aggregate_sectors,
    aggregates_to_history_records,
)

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


@pytest.fixture(scope="module")
def scored_v2():
    df = load_koyfin_csv(FIXTURE)
    settings = Settings()
    scored = compute_scores(df, settings)
    out, _ = compute_scores_v2(scored, settings)
    return out


def test_aggregate_sectors_v2_levels(scored_v2):
    agg = aggregate_sectors(scored_v2, score_col="composite_score")
    assert agg, "keine Sektor-Aggregate"
    assert all(s["history_level"] == "sector_v2" for s in agg)
    records = aggregates_to_history_records(agg)
    levels = {r["level"] for r in records}
    assert levels <= {"sector_v2", "industry_v2"}


def test_aggregate_sectors_v1_levels_unchanged(scored_v2):
    agg = aggregate_sectors(scored_v2)
    records = aggregates_to_history_records(agg)
    levels = {r["level"] for r in records}
    assert levels <= {"sector", "industry"}


def test_aggregate_sectors_unknown_score_col_falls_back(scored_v2):
    agg = aggregate_sectors(scored_v2, score_col="gibts_nicht")
    assert agg and agg[0]["history_level"] == "sector"


def test_factsheet_context_contains_v2(scored_v2):
    from app.core.factsheet_pdf import build_context

    ticker = str(scored_v2.iloc[0]["uid"])
    ctx = build_context(ticker, scored_v2, Settings(), show_peers=False)
    v2 = ctx["v2"]
    assert v2 is not None
    assert v2["zone"] in {"KANDIDAT", "HALTEN", "VERKAUFEN", "FILTER", "–"}
    assert len(v2["factors"]) == 4
    assert v2["class_accent"].startswith("#")
    assert v2["zone_accent"].startswith("#")


def test_factsheet_context_without_v2_columns():
    from app.core.factsheet_pdf import build_context

    df = load_koyfin_csv(FIXTURE)
    scored = compute_scores(df, Settings())
    ticker = str(scored.iloc[0]["uid"])
    ctx = build_context(ticker, scored, Settings(), show_peers=False)
    assert ctx["v2"] is None


def test_compute_rank_score_col(scored_v2):
    from app.core.factsheet_pdf import compute_rank

    ticker = str(scored_v2.iloc[0]["uid"])
    rank_v1 = compute_rank(scored_v2, ticker)
    rank_v2 = compute_rank(scored_v2, ticker, score_col="composite_score")
    assert rank_v1["sector_total"] == rank_v2["sector_total"]
