"""Tests 5–7: PIT-Verfügbarkeit, kein Look-ahead, Snapshot-Schema."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from app.backtest.pit_builder import (
    SNAPSHOT_COLUMNS,
    AlphaVantageSnapshotSource,
    SnapshotSource,
    write_snapshot_csv,
)
from app.backtest.simulator import risk_cache_from_backtest
from app.core.config import Settings
from app.core.data_loader import load_koyfin_csv
from app.core.scoring import compute_scores
from app.core.scoring_v2 import compute_scores_v2, derive_v2_indicators
from app.core.universe_filter import apply_universe_filters

from .conftest import make_dataset, small_config


def test_pit_availability(dataset, config):
    """Abschluss wird erst fiscal_date + 90 sichtbar; 18-Monats-Regel (Test 5)."""
    # fiscal 2013-12-31 → verfügbar ab 2014-03-31.
    cur, prev = dataset.fundamentals_at("T00", date(2014, 3, 30), 90, 18)
    assert cur["fiscal_date"] == pd.Timestamp("2012-12-31")
    assert prev["fiscal_date"] == pd.Timestamp("2011-12-31")
    cur, prev = dataset.fundamentals_at("T00", date(2014, 3, 31), 90, 18)
    assert cur["fiscal_date"] == pd.Timestamp("2013-12-31")
    assert prev["fiscal_date"] == pd.Timestamp("2012-12-31")
    # Lag 120 (Sensitivität S9): erst ab 2014-04-30.
    cur, _ = dataset.fundamentals_at("T00", date(2014, 4, 29), 120, 18)
    assert cur["fiscal_date"] == pd.Timestamp("2012-12-31")

    # 18-Monats-Regel: nur Abschlüsse bis 2012 vorhanden, d = 2014-09-30 → NaN.
    ds = make_dataset(n=5, fiscal_years=(2010, 2011, 2012))
    cur, prev = ds.fundamentals_at("T00", date(2014, 6, 29), 90, 18)
    assert cur is not None and cur["fiscal_date"] == pd.Timestamp("2012-12-31")
    cur, prev = ds.fundamentals_at("T00", date(2014, 9, 30), 90, 18)
    assert cur is None and prev is None
    snap = AlphaVantageSnapshotSource(ds, small_config()).build_snapshot(date(2014, 9, 30))
    # Ohne verfügbaren Abschluss keine Marktkapitalisierung → Titel fällt aus
    # dem Universum (Größenfilter braucht shares_out); Snapshot ist leer.
    assert snap.empty or snap["net_income"].isna().all()

    # Snapshot-Spalten aktuell/vorjahr folgen derselben Regel.
    src = AlphaVantageSnapshotSource(dataset, config)
    s1 = src.build_snapshot(date(2014, 3, 28)).set_index("ticker")
    s2 = src.build_snapshot(date(2014, 3, 31)).set_index("ticker")
    f = dataset.fundamentals["T00"].set_index("fiscal_date")
    fx1, fx2 = dataset.fx.rate(date(2014, 3, 28)), dataset.fx.rate(date(2014, 3, 31))
    assert s1.loc["T00", "revenue"] == pytest.approx(f.loc["2012-12-31", "revenue"] * fx1 / 1e6)
    assert s2.loc["T00", "revenue"] == pytest.approx(f.loc["2013-12-31", "revenue"] * fx2 / 1e6)
    assert s2.loc["T00", "revenue_prev"] == pytest.approx(f.loc["2012-12-31", "revenue"] * fx2 / 1e6)


def test_no_lookahead(dataset, config):
    """Snapshot(d) ändert sich nicht, wenn Daten nach d verändert werden (Test 6)."""
    d = date(2014, 6, 30)
    src = AlphaVantageSnapshotSource(dataset, config)
    before = src.build_snapshot(d)
    cache_before = risk_cache_from_backtest(dataset, d, ["T00", "T01"])

    # Kurse nach d verändern (Verdopplung) und Kalender verlängern.
    ts = pd.Timestamp(d)
    future = dataset.calendar > ts
    for name in ("adj_close", "close", "volume"):
        frame = getattr(dataset, name)
        frame.loc[future] = frame.loc[future] * 2.0
    extra = pd.bdate_range(dataset.calendar[-1] + pd.Timedelta(days=1), periods=30)
    for name in ("adj_close", "close", "volume"):
        frame = getattr(dataset, name)
        add = pd.DataFrame(frame.iloc[-1].to_dict(), index=extra) * 3.0
        setattr(dataset, name, pd.concat([frame, add]))
    dataset.calendar = dataset.adj_close.index
    # FX nach d verändern.
    fx = dataset.fx.series.copy()
    fx.loc[fx.index > ts] = 0.5
    dataset.fx.series = fx
    # Zukünftiger Jahresabschluss mit abwegigen Werten.
    for t, f in dataset.fundamentals.items():
        row = f.iloc[-1].copy()
        row["fiscal_date"] = pd.Timestamp("2014-06-30")  # verfügbar erst 2014-09-28
        row["net_income"] = row["net_income"] * 100
        dataset.fundamentals[t] = pd.concat([f, row.to_frame().T], ignore_index=True)
    # Späteres Listing mit zusätzlichem Ticker.
    later = date(2014, 9, 30)
    frame = dataset.listings[max(k for k in dataset.listings if k <= d)].copy()
    dataset.listings[later] = pd.concat(
        [frame, pd.DataFrame([{"symbol": "ZZZZ", "name": "Future Co", "exchange": "NYSE",
                               "asset_type": "Stock", "ipo_date": pd.Timestamp("2011-01-03"),
                               "delisting_date": pd.NaT, "status": "Active"}])],
        ignore_index=True,
    )

    src2 = AlphaVantageSnapshotSource(dataset, config)
    after = src2.build_snapshot(d)
    pd.testing.assert_frame_equal(before, after)

    cache_after = risk_cache_from_backtest(dataset, d, ["T00", "T01"])
    pd.testing.assert_frame_equal(cache_before["returns"], cache_after["returns"])
    pd.testing.assert_series_equal(cache_before["bm_returns"], cache_after["bm_returns"])
    assert cache_after["returns"].index.max() <= ts


def test_snapshot_schema(dataset, config):
    """Snapshot enthält exakt die Import-Spalten; Loader-Roundtrip;
    derive_v2_indicators und apply_universe_filters laufen fehlerfrei (Test 7)."""
    assert issubclass(AlphaVantageSnapshotSource, SnapshotSource)
    d = date(2014, 3, 31)
    src = AlphaVantageSnapshotSource(dataset, config)
    snap = src.build_snapshot(d)
    assert list(snap.columns) == [*SNAPSHOT_COLUMNS, "uid"]
    assert (snap["region"] == "United States").all()
    assert snap["eps_revisions_3m"].isna().all()
    assert (snap["export_date"] == d.isoformat()).all()
    assert snap["uid"].is_unique

    # Der produktive Import liest die CSV fehlerfrei und liefert dieselben Werte.
    csv = write_snapshot_csv(snap)
    loaded = load_koyfin_csv(csv.encode("utf-8"))
    assert list(loaded.columns) == list(snap.columns)
    for col in ("market_cap", "pe", "volatility_1y", "ret_12m", "fcf_yield", "ev_ebit",
                "net_debt_ebitda", "adv_3m", "net_income_prev"):
        np.testing.assert_allclose(
            pd.to_numeric(loaded[col], errors="coerce").to_numpy(),
            pd.to_numeric(snap[col], errors="coerce").to_numpy(),
            rtol=1e-9, equal_nan=True, err_msg=col,
        )
    assert loaded["ipo_date"].iloc[0] == snap["ipo_date"].iloc[0]

    settings = config.settings()
    derived, diags = derive_v2_indicators(loaded, settings)
    assert {"gp_ta", "accruals", "asset_growth", "share_issuance", "mom_12_1_adj"} <= set(derived.columns)
    assert not any(d.code == "optional_column_missing" and "ev_ebit" in d.message for d in diags)
    scored = compute_scores(loaded, settings)
    scored, _ = compute_scores_v2(scored, settings, snapshot_date=d)
    filtered, _ = apply_universe_filters(scored, settings, snapshot_date=d)
    assert "filter_pass" in filtered.columns and filtered["filter_pass"].any()
    assert (filtered["data_coverage_v2"] >= 0.6).mean() >= 0.85
    assert filtered["composite_z"].notna().sum() >= 20

    # Momentum-Proxy (S8) belegt eps_revisions_3m innerhalb des Gültigkeitsbands.
    cfg8 = small_config(bt_momentum_proxy="mom_6_1_adj")
    snap8 = AlphaVantageSnapshotSource(dataset, cfg8).build_snapshot(d)
    assert snap8["eps_revisions_3m"].notna().any()
    assert snap8["eps_revisions_3m"].abs().max() <= 1.0
