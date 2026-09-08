"""Test 17: Paper-Portfolio — weight_model/weight_effective, tägliche
Bewertung, fehlende Kurse gezählt."""

from __future__ import annotations

import importlib
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.backtest import paper
from app.backtest.config import BacktestConfig
from app.core import av_store, persistence
from app.core.config import Settings


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/paper.db")
    importlib.reload(persistence)
    return persistence


def _portfolio(w_model: dict[str, float], w_eff: dict[str, float]) -> pd.DataFrame:
    uids = sorted(set(w_model) | set(w_eff))
    return pd.DataFrame(
        {
            "uid": uids,
            "composite_z": 1.0, "composite_pct": 0.9, "zone_v2": "KANDIDAT",
            "weight_model": [w_model.get(u, 0.0) for u in uids],
            "weight_effective": [w_eff.get(u, 0.0) for u in uids],
            "cte": 0.01, "action": "KAUF", "reason": "zone_KANDIDAT",
            "rebalance_mode": "full", "override_id": None,
        }
    )


def _meta(mode: str) -> dict:
    return {"rebalance_mode": mode, "n_titles": 2, "te_ex_ante": 0.05, "te_coverage": 1.0,
            "turnover_oneway": 0.1, "n_trades": 2, "n_deferred": 0,
            "settings_hash": "x", "diagnostics": "[]"}


def test_paper_update(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    settings = Settings()
    settings.risk_benchmark_symbol = "ACWI"
    cal = pd.bdate_range("2026-03-02", "2026-06-30")
    now = datetime(2026, 7, 1, 8, 0)

    def _series(start: float, drift: float) -> pd.Series:
        return pd.Series(start * np.cumprod(np.full(len(cal), 1 + drift)), index=cal)

    prices = {"AAA": _series(100.0, 0.001), "BBB": _series(50.0, -0.0005),
              "CCC": _series(20.0, 0.002), "ACWI": _series(120.0, 0.0004)}
    # BBB: Kurslücke von 8 Tagen → über ffill-Grenze hinaus fehlende Kurse.
    gap = cal[(cal >= "2026-05-04") & (cal <= "2026-05-13")]
    prices["BBB"] = prices["BBB"].drop(gap)
    for sym, s in prices.items():
        av_store.save_prices(sym, pd.DataFrame({"adj_close": s, "close": s}))
        av_store.set_symbol_meta(sym, "USD", "aktie", cal[-1].date(), now)
        if sym != "ACWI":
            av_store.save_av_mapping(sym, sym, "USD", confirmed=True)
    fx = pd.Series(0.9, index=cal)
    av_store.save_prices("FX:USDEUR", pd.DataFrame({"adj_close": fx}))
    av_store.set_symbol_meta("FX:USDEUR", None, "reihe", cal[-1].date(), now)

    # Zwei Rebalancing-Snapshots (full, interim) + ein monitor-Lauf (ignoriert).
    p.save_model_portfolio(_portfolio({"AAA": 0.5, "BBB": 0.5}, {"AAA": 0.8, "BBB": 0.2}),
                           _meta("full"), date(2026, 3, 31))
    p.save_model_portfolio(_portfolio({"AAA": 0.4, "CCC": 0.6}, {"AAA": 0.7, "CCC": 0.3}),
                           _meta("interim"), date(2026, 5, 29))
    p.save_model_portfolio(_portfolio({"CCC": 1.0}, {"CCC": 1.0}), _meta("monitor"), date(2026, 6, 15))

    targets = paper.load_targets()
    assert sorted(targets["model"]) == [date(2026, 3, 31), date(2026, 5, 29)]
    assert targets["model"][date(2026, 3, 31)] == {"AAA": 0.5, "BBB": 0.5}
    assert targets["effective"][date(2026, 3, 31)] == pytest.approx({"AAA": 0.8, "BBB": 0.2})

    cfg = BacktestConfig(bt_initial_capital=1_000_000.0)
    summary = paper.update_paper(settings=settings, config=cfg, fetch=False, asof=date(2026, 6, 30))
    assert summary["rows"] > 0 and summary["unresolved"] == [] and summary["missing_cache"] == []
    assert summary["start"] == "2026-03-31"

    nav = paper.load_paper_nav()
    assert set(nav["variant"]) == {"model", "effective"}
    model = nav[nav["variant"] == "model"]
    eff = nav[nav["variant"] == "effective"]
    assert model.index.min().date() == date(2026, 3, 31)
    assert model.index.max().date() == date(2026, 6, 30)
    assert len(model) == len(cal[cal >= "2026-03-31"])
    # Beide Varianten starten beim Startkapital und entwickeln sich unterschiedlich.
    assert model["nav"].iloc[0] == pytest.approx(1_000_000.0, rel=0.01)
    assert not np.allclose(model["nav"].to_numpy(), eff["nav"].to_numpy())
    # AAA steigt, BBB fällt: die effektive Variante (80 % AAA) liegt vorn.
    assert eff["nav"].iloc[-1] > model["nav"].iloc[-1]
    # Benchmark-NAV folgt ACWI in EUR (normiert auf Startkapital).
    assert model["benchmark_nav"].iloc[0] == pytest.approx(1_000_000.0)
    assert model["benchmark_nav"].iloc[-1] == pytest.approx(
        1_000_000.0 * prices["ACWI"].loc["2026-06-30"] / prices["ACWI"].loc["2026-03-31"], rel=1e-6
    )
    # Fehlende Kurse (BBB-Lücke > 3 Tage) werden gezählt und fortgeschrieben.
    assert summary["missing_prices"]["model"] > 0
    assert model["missing_prices"].sum() == summary["missing_prices"]["model"]
    assert model.loc["2026-05-04":"2026-05-13", "nav"].notna().all()
    # Nach dem zweiten Snapshot: BBB verkauft, CCC gekauft.
    assert int(model.loc["2026-05-29", "n_positions"]) == 2
    assert int(model.loc["2026-06-30", "n_positions"]) == 2

    # Track Record: Kennzahlen beider Varianten plus Override-Beitrag.
    tr = paper.track_record()
    assert set(tr["variants"]) == {"model", "effective"}
    assert tr["override_contribution"]["total_return"] == pytest.approx(
        tr["variants"]["effective"]["portfolio"]["total_return"]
        - tr["variants"]["model"]["portfolio"]["total_return"]
    )
    md = paper.build_paper_report()
    assert md.startswith("# Paper-Portfolio") and "Override-Beitrag" in md

    # Idempotent: erneutes Update ersetzt, verdoppelt nicht.
    paper.update_paper(settings=settings, config=cfg, fetch=False, asof=date(2026, 6, 30))
    assert len(paper.load_paper_nav()) == len(nav)
