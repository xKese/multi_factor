"""Tests des Komitee-Bericht-Builders (``app.core.committee_report``)."""

from __future__ import annotations

import importlib
import inspect
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from app.core import committee_report as cr
from app.core.config import Settings

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_universe_sample.csv"
ASOF = date(2026, 4, 10)  # Export-Datum des Fixture-Universums
PREV = ASOF - timedelta(days=7)
MONTH = ASOF - timedelta(days=35)
N_ZONE_FLIPS = 5
N_DROPPED = 2


# ── Fixtures ───────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def scored_universe() -> pd.DataFrame:
    from app.core.data_loader import load_koyfin_csv
    from app.core.scoring import compute_scores
    from app.core.scoring_v2 import compute_scores_v2

    raw = load_koyfin_csv(FIXTURE.read_bytes())
    scored = compute_scores(raw, Settings())
    scored, _ = compute_scores_v2(scored, Settings(), snapshot_date=ASOF)
    return scored


@pytest.fixture()
def env(tmp_path, monkeypatch, scored_universe):
    """Frische SQLite-DB mit drei archivierten Snapshots, Portfolio und
    Modellportfolio; liefert einen befüllten ``AppState``."""
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("KOYFIN_ARCHIVE_DIR", str(tmp_path / "archive"))
    from app.core import persistence, signal_events

    importlib.reload(persistence)
    signal_events.clear_cache()
    from app.core.data_loader import load_koyfin_csv
    from app.core.sector_momentum import aggregate_sectors, aggregates_to_history_records
    from app.core.state import AppState

    raw = load_koyfin_csv(FIXTURE.read_bytes())
    state = AppState()
    state.settings = Settings()
    state.set_raw(raw)
    df = state.scored
    cands = df[df["zone_v2"] == "KANDIDAT"].sort_values("composite_z", ascending=False)["uid"].tolist()

    def _perturb(frame, shift, drop=(), flips=()):
        f = frame.copy()
        f["composite_score"] = (f["composite_score"] + shift).clip(0, 100)
        for u in flips:
            f.loc[f["uid"] == u, "zone_v2"] = "HALTEN"
        return f[~f["uid"].isin(drop)]

    old = _perturb(df, -3.0, drop=cands[:N_DROPPED], flips=cands[N_DROPPED:N_DROPPED + N_ZONE_FLIPS])
    mid = _perturb(df, 1.0, drop=cands[:1])
    for snap, frame in ((MONTH, old), (PREV, mid), (ASOF, df)):
        persistence.save_universe(state.raw, snapshot_date=snap, archive_df=frame)
        persistence.save_sector_score_history(
            aggregates_to_history_records(aggregate_sectors(frame, score_col="composite_score")), snap
        )

    tickers = df.sort_values("composite_z", ascending=False)["ticker"].head(12).tolist()
    pf = pd.DataFrame({"ticker": tickers + ["ZZZZ"], "name": [""] * 13, "weight": [1 / 13] * 13})
    pid, _n = persistence.save_ms_portfolio_named(pf, "Testdepot")
    persistence.set_portfolio_selection(persistence.SELECTION_ACTIVE, pid)
    state.refresh_portfolios()
    state.set_ms_portfolio(pf, portfolio_id=pid)

    mp_rows = df[df["zone_v2"] == "KANDIDAT"].sort_values("composite_z", ascending=False).head(30)
    mp = pd.DataFrame(
        {
            "uid": mp_rows["uid"], "composite_z": mp_rows["composite_z"], "composite_pct": mp_rows["composite_pct"],
            "zone_v2": mp_rows["zone_v2"], "weight_model": 1 / 30, "weight_effective": 1 / 30, "cte": 0.001,
            "action": "KAUF", "reason": "zone_KANDIDAT", "rebalance_mode": "full", "override_id": None,
        }
    )
    meta = {
        "rebalance_mode": "full", "n_titles": 30, "te_ex_ante": 0.05, "te_coverage": 0.9, "turnover_oneway": 0.2,
        "n_trades": 30, "n_deferred": 0, "settings_hash": persistence.settings_hash_v2(state.settings),
        "diagnostics": "[]", "source_portfolio_id": pid, "source_portfolio_name": "Testdepot",
        "neut_scheme_hash": state.settings.neut_scheme_hash(), "pc_profile_id": None, "pc_profile_name": None,
    }
    persistence.save_model_portfolio(mp, meta, ASOF)
    return state


@pytest.fixture()
def report(env):
    return cr.build_committee_report(env, cr.ReportOptions(), today=ASOF + timedelta(days=3))


# ── Snapshot-Auswahl ───────────────────────────────────────────────────────


def _snaps(*days: int) -> list[tuple[date, int]]:
    return [(ASOF - timedelta(days=d), 100) for d in days]


def test_pick_snapshots_auto():
    out = cr.pick_comparison_snapshots(_snaps(0, 7, 35, 70), ASOF)
    assert out["prev"] == ASOF - timedelta(days=7)
    assert out["month"] == ASOF - timedelta(days=35)
    assert out["month_offset_days"] == 5
    assert not out["same_as_prev"]


def test_pick_snapshots_excludes_asof_itself_and_handles_empty():
    out = cr.pick_comparison_snapshots(_snaps(0), ASOF)
    assert out["prev"] is None and out["month"] is None
    assert "Kein früherer Snapshot" in out["prev_note"]


def test_pick_snapshots_tolerance_and_tie():
    # 50 Tage entfernt → außerhalb ± 15 → kein 1M-Vergleich.
    out = cr.pick_comparison_snapshots(_snaps(0, 80), ASOF)
    assert out["prev"] == ASOF - timedelta(days=80)
    assert out["month"] is None
    assert "kein 1M-Vergleich" in out["month_note"]
    # Gleicher Abstand (25 und 35 Tage) → das ältere Datum gewinnt.
    out = cr.pick_comparison_snapshots(_snaps(0, 25, 35), ASOF)
    assert out["month"] == ASOF - timedelta(days=35)


def test_pick_snapshots_same_as_prev_and_overrides():
    out = cr.pick_comparison_snapshots(_snaps(0, 30), ASOF)
    assert out["same_as_prev"] and out["month"] == out["prev"]
    out = cr.pick_comparison_snapshots(
        _snaps(0, 7, 35, 70), ASOF, prev_override=ASOF - timedelta(days=35), month_override=ASOF - timedelta(days=70)
    )
    assert out["prev"] == ASOF - timedelta(days=35)
    assert out["month"] == ASOF - timedelta(days=70)
    # Unbekannter Override → automatisch, mit Hinweis.
    out = cr.pick_comparison_snapshots(_snaps(0, 7), ASOF, prev_override=ASOF - timedelta(days=3))
    assert out["prev"] == ASOF - timedelta(days=7)
    assert "nicht im Archiv" in out["prev_note"]


# ── Reine Hilfsfunktionen ──────────────────────────────────────────────────


def test_rrg_quadrant():
    assert cr.rrg_quadrant(5, 3) == cr.RRG_LEADING
    assert cr.rrg_quadrant(5, -1) == cr.RRG_WEAKENING
    assert cr.rrg_quadrant(-2, 4) == cr.RRG_IMPROVING
    assert cr.rrg_quadrant(-2, -4) == cr.RRG_LAGGING
    assert cr.rrg_quadrant(None, 1) == cr.RRG_NONE
    assert cr.rrg_quadrant(float("nan"), 1) == cr.RRG_NONE


def test_normalize_snapshot_frame_decodes_json_lists():
    df = pd.DataFrame({"uid": ["A", "A", "B"], "ticker": ["A", "A", "B"], "filter_reasons": ['["x"]', '["x"]', None], "filter_pass": [1, 1, 0]})
    out = cr.normalize_snapshot_frame(df)
    assert list(out["uid"]) == ["A", "B"]
    assert out["filter_reasons"].tolist() == [["x"], []]
    assert out["filter_pass"].tolist() == [True, False]
    assert cr.normalize_snapshot_frame(None) is None


def test_stock_deltas_and_zone_matrix():
    cols = cr.resolve_score_columns(Settings(), pd.DataFrame({"composite_score": [1.0]}))
    now = pd.DataFrame(
        {
            "uid": ["A", "B", "C"], "ticker": ["A", "B", "C"], "composite_score": [80.0, 60.0, 40.0],
            "zone_v2": ["KANDIDAT", "HALTEN", "VERKAUFEN"], "classification_v2": ["A", "B", "D"], "filter_pass": [True, True, True],
        }
    )
    prev = pd.DataFrame(
        {
            "uid": ["A", "B", "D"], "ticker": ["A", "B", "D"], "composite_score": [70.0, 65.0, 50.0],
            "zone_v2": ["HALTEN", "HALTEN", "FILTER"], "classification_v2": ["B", "B", "C"], "filter_pass": [True, True, False],
        }
    )
    deltas = cr.compute_stock_deltas(now, prev, cols)
    by = deltas.set_index("uid")
    assert by.loc["A", "delta_score"] == 10.0 and by.loc["A", "status"] == cr.STATUS_BOTH
    assert bool(by.loc["A", "zone_changed"]) and not bool(by.loc["B", "zone_changed"])
    assert by.loc["C", "status"] == cr.STATUS_NEW and by.loc["D", "status"] == cr.STATUS_GONE
    m = cr.zone_transition_matrix(deltas)
    assert list(m.index) == list(cr.ZONES) + ["neu"]
    assert list(m.columns) == list(cr.ZONES) + ["weg"]
    assert int(m.loc["HALTEN", "KANDIDAT"]) == 1
    assert int(m.loc["HALTEN", "HALTEN"]) == 1
    assert int(m.loc["neu", "VERKAUFEN"]) == 1
    assert int(m.loc["FILTER", "weg"]) == 1
    assert int(m.to_numpy().sum()) == 4


def test_resolve_score_columns_v1_fallback():
    s = Settings()
    s.scoring_version = "v1"
    cols = cr.resolve_score_columns(s, pd.DataFrame({"composite_score": [1.0], "total_score": [1.0]}))
    assert cols["version"] == "v1" and cols["score"] == "total_score" and cols["z"] is None
    cols = cr.resolve_score_columns(Settings(), pd.DataFrame({"total_score": [1.0]}))
    assert cols["version"] == "v1"


def test_core_module_is_dash_free():
    src = inspect.getsource(cr)
    assert "import dash" not in src and "from dash" not in src
    assert "from app.ui import" not in src


# ── Vollständiger Bericht ──────────────────────────────────────────────────


def test_report_sections_and_meta(report):
    for key in ("meta", "key_findings", "universe", "changes", "sectors", "portfolio", "model_portfolio", "factor_timing", "risk", "agents", "data_quality", "parameters", "diagnostics"):
        assert key in report
    meta = report["meta"]
    assert meta["asof"] == ASOF and meta["scoring_version"] == "v2"
    assert meta["comparison"]["prev"] == PREV and meta["comparison"]["month"] == MONTH
    assert meta["portfolio_name"] == "Testdepot"
    assert meta["filename_stem"] == f"Investment-Komitee-Bericht_Testdepot_{ASOF.isoformat()}"
    assert report["risk"]["available"] is False  # Opt-in
    assert report["factor_timing"]["available"] is False  # keine Historie
    assert report["agents"]["available"] is False


def test_report_changes_reflect_synthetic_history(report):
    vs_month = report["changes"]["vs_month"]
    assert vs_month["available"] and vs_month["snapshot_date"] == MONTH
    # 5 Kandidaten waren im ~1M-Snapshot HALTEN, 2 fehlten → beide gelten heute als neue Kandidaten.
    assert len(vs_month["new_candidates"]) == N_ZONE_FLIPS + N_DROPPED
    assert vs_month["n_new"] == N_DROPPED and vs_month["n_gone"] == 0
    assert int(vs_month["zone_matrix"].loc["HALTEN", "KANDIDAT"]) == N_ZONE_FLIPS
    assert int(vs_month["zone_matrix"].loc["neu", "KANDIDAT"]) == N_DROPPED
    assert vs_month["spearman_rho"] is not None and vs_month["spearman_rho"] > 0.99
    # Score-Shift −3 im alten Snapshot → Ø-Delta ≈ +3 (abgesehen vom Clip).
    assert 2.5 <= vs_month["avg_abs_delta"] <= 3.0
    vs_prev = report["changes"]["vs_prev"]
    assert vs_prev["snapshot_date"] == PREV and vs_prev["n_new"] == 1
    assert vs_prev["n_zone_changed"] == 0


def test_report_universe_and_watchlist(report):
    uni = report["universe"]
    assert uni["n"] == report["meta"]["n_universe"]
    assert sum(uni["zone_counts"].values()) == uni["n"]
    assert len(uni["top_candidates"]) == cr.DEFAULT_TOP_N
    assert "delta_prev" in uni["stock_columns"] and "in_portfolio" in uni["stock_columns"]
    assert any(r["in_portfolio"] for r in uni["top_candidates"])
    entry = report["changes"]["watchlist_entry"]
    assert all(r["zone_v2"] == "HALTEN" for r in entry)
    assert all(0.78 <= r["composite_pct"] < 0.80 for r in entry)


def test_report_sectors(report):
    sec = report["sectors"]
    assert sec["available"] and len(sec["rows"]) >= 10
    row = sec["rows"][0]
    assert row["rrg"] in (cr.RRG_LEADING, cr.RRG_IMPROVING, cr.RRG_WEAKENING, cr.RRG_LAGGING)
    assert row["delta_prev"] is not None and row["delta_month"] is not None
    pf_total = sum((r["pf_weight"] or 0.0) for r in sec["rows"])
    assert abs(pf_total - report["portfolio"]["weight_resolved"]) < 1e-6
    assert sum(sec["quadrant_counts"].values()) == len(sec["rows"])


def test_report_portfolio(report):
    pf = report["portfolio"]
    assert pf["available"] and pf["name"] == "Testdepot"
    assert pf["n_positions"] == 13 and pf["n_resolved"] == 12
    assert pf["missing"] == ["ZZZZ"]
    assert len(pf["positions"]) == 12
    assert abs(sum(r["portfolio"] for r in pf["sector_exposure"]) - pf["weight_resolved"]) < 1e-9
    assert abs(pf["delta_weighted_month"] - 3.0) < 0.6
    assert pf["weighted_avg_score"] > pf["universe_avg_score"]
    assert pf["concentration"]["n_positions"] == 12
    assert pf["concentration"]["n_above_cap"] == 12  # 1/13 > 5 %
    assert len(pf["factor_exposure"]) == 4
    assert {"flags", "severity", "delta_prev", "zone_prev", "agent_rating"} <= set(pf["position_columns"])


def test_report_model_portfolio_reconstructs_sells(report):
    mp = report["model_portfolio"]
    assert mp["available"] and mp["run_date"] == ASOF
    assert mp["settings_hash_matches"] is True
    assert mp["source_portfolio_name"] == "Testdepot"
    actions = {t["action"] for t in mp["trades"]}
    assert "VERKAUF" in actions  # ZZZZ und Bestandstitel ∉ Ziel
    sells = [t for t in mp["trades"] if t["action"] == "VERKAUF"]
    assert any(t["uid"] == "ZZZZ" for t in sells)
    assert mp["n_sell"] == len(sells)
    assert "KAUF" in actions or "VERSCHOBEN" in actions
    assert abs(sum(r["portfolio"] for r in mp["sector_exposure"]) - 1.0) < 1e-6


def test_report_data_quality_and_parameters(report):
    dq = report["data_quality"]
    assert dq["available"] and dq["n_snapshots"] == 3 and dq["import_age_days"] == 3
    assert dq["portfolio_missing"] == ["ZZZZ"]
    assert dq["neut_scheme_drift"] == []
    pr = report["parameters"]
    assert pr["available"] and pr["settings_hash"] == report["meta"]["settings_hash"]
    titles = [g["title"] for g in pr["groups"]]
    assert "Portfoliokonstruktion" in titles and "Universumsfilter" in titles


def test_key_findings_are_german_with_decimal_comma(report):
    findings = report["key_findings"]
    assert 3 <= len(findings) <= cr.MAX_KEY_FINDINGS
    text = " ".join(f["text"] for f in findings)
    assert "Testdepot" in text
    assert "ZZZZ" in text  # fehlender Ticker wird gemeldet
    assert "," in text
    # Prozentzahlen mit Dezimalpunkt dürfen nicht vorkommen.
    import re

    assert not re.search(r"\d\.\d+ %", text)
    assert all(f["tone"] in ("up", "down", "warn", "info") for f in findings)


def test_report_without_history_and_model_portfolio(tmp_path, monkeypatch, scored_universe):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/empty.db")
    from app.core import persistence, signal_events

    importlib.reload(persistence)
    signal_events.clear_cache()
    from app.core.data_loader import load_koyfin_csv
    from app.core.state import AppState

    state = AppState()
    state.set_raw(load_koyfin_csv(FIXTURE.read_bytes()))
    rep = cr.build_committee_report(state, cr.ReportOptions(include_risk=True))
    assert rep["changes"]["vs_prev"]["available"] is False
    assert rep["changes"]["vs_month"]["available"] is False
    assert rep["model_portfolio"]["available"] is False
    assert rep["risk"]["available"] is False and "Kurscache" in rep["risk"]["note"]
    assert rep["portfolio"]["available"]  # Fallback-Liste des AppState
    assert rep["universe"]["available"] and rep["key_findings"]


def test_report_v1_scoring_path(env):
    env.settings.scoring_version = "v1"
    rep = cr.build_committee_report(env, cr.ReportOptions(include_model_portfolio=False), today=ASOF)
    assert rep["meta"]["scoring_version"] == "v1"
    assert set(rep["universe"]["zone_counts"]) == set(cr.V1_RECOMMENDATIONS)
    assert rep["changes"]["vs_prev"]["available"]
    assert rep["portfolio"]["available"]


def test_snapshot_without_v2_columns_is_unavailable(env):
    from app.core import persistence

    old = date(2025, 1, 1)
    persistence.save_universe(env.raw, snapshot_date=old, archive_df=env.raw)
    rep = cr.build_committee_report(env, cr.ReportOptions(prev_snapshot=old), today=ASOF)
    assert rep["changes"]["vs_prev"]["available"] is False
    assert "composite_score" in rep["changes"]["vs_prev"]["note"]


def test_build_requires_universe():
    from app.core.state import AppState

    with pytest.raises(ValueError):
        cr.build_committee_report(AppState(), cr.ReportOptions())
