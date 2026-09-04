"""Smoke-Tests für compute_peers (similar vs. top_score)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.peers import compute_peers
from app.core.scoring import compute_scores
from app.core.scoring_v2 import compute_scores_v2


FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


def _scored_fixture() -> pd.DataFrame:
    df = load_koyfin_csv(FIXTURE.read_bytes())
    return compute_scores(df, Settings())


def _scored_v2_fixture() -> pd.DataFrame:
    settings = Settings()
    out, _ = compute_scores_v2(_scored_fixture(), settings)
    return out


def test_similar_mode_sorts_by_distance_ascending():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    out = compute_peers(scored, ticker, n=6, mode="similar")

    assert not out.empty
    assert ticker not in set(out["ticker"]), "Ziel-Ticker darf nicht in Peers sein"
    distances = out["distance"].to_numpy()
    assert np.all(np.diff(distances) >= 0), "Distanz muss aufsteigend sortiert sein"


def test_top_score_mode_sorts_by_total_score_descending():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    out = compute_peers(scored, ticker, n=6, mode="top_score")

    assert not out.empty
    scores = out["total_score"].dropna().to_numpy()
    assert np.all(np.diff(scores) <= 0), "total_score muss absteigend sortiert sein"


def test_modes_share_pool_size():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    sim = compute_peers(scored, ticker, n=6, mode="similar")
    top = compute_peers(scored, ticker, n=6, mode="top_score")
    assert len(sim) == len(top), "Beide Modi müssen denselben Pool ausschöpfen"


def test_top_score_puts_nan_last():
    scored = _scored_fixture().copy()
    ticker = scored["ticker"].iloc[0]
    others = scored.index[scored["ticker"] != ticker]
    if len(others) >= 2:
        scored.loc[others[:2], "total_score"] = np.nan

    out = compute_peers(scored, ticker, n=6, mode="top_score")
    if out["total_score"].isna().any():
        first_nan = out["total_score"].isna().to_numpy().argmax()
        rest = out["total_score"].iloc[first_nan:]
        assert rest.isna().all(), "NaN-total_score muss am Ende stehen"


def test_default_mode_is_similar():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    default = compute_peers(scored, ticker, n=6)
    explicit = compute_peers(scored, ticker, n=6, mode="similar")
    pd.testing.assert_frame_equal(default, explicit)


def test_v2_similar_uses_z_space():
    scored = _scored_v2_fixture()
    ticker = scored["ticker"].iloc[0]
    out = compute_peers(scored, ticker, n=6, mode="similar", version="v2")

    assert not out.empty
    assert {"z_value", "z_quality", "z_momentum", "z_investment"} <= set(
        out.columns
    )
    assert "composite_score" in out.columns
    distances = out["distance"].to_numpy()
    assert np.all(np.diff(distances) >= 0), "Distanz muss aufsteigend sortiert sein"


def test_v2_top_score_sorts_by_composite_descending():
    scored = _scored_v2_fixture()
    ticker = scored["ticker"].iloc[0]
    out = compute_peers(scored, ticker, n=6, mode="top_score", version="v2")

    assert not out.empty
    scores = out["composite_score"].dropna().to_numpy()
    assert np.all(np.diff(scores) <= 0), "composite_score muss absteigend sortiert sein"


def test_v2_falls_back_to_v1_without_v2_columns():
    scored = _scored_fixture()
    ticker = scored["ticker"].iloc[0]
    fallback = compute_peers(scored, ticker, n=6, version="v2")
    v1 = compute_peers(scored, ticker, n=6, version="v1")
    pd.testing.assert_frame_equal(fallback, v1)
