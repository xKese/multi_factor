"""Tests für Report-Orchestrierung, Markdown-Builder und CLI."""

from __future__ import annotations

import importlib
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from app.core.config import Settings
from app.core.state import AppState

N_DAYS = 320
DAYS = pd.bdate_range("2022-10-03", periods=N_DAYS)
ASOF = DAYS[-1].date()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Frische DB mit gefülltem Kurscache (2 Titel, Benchmark, FX, Makro)."""

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    from app.core import av_store, market_data, persistence, risk_report

    importlib.reload(persistence)
    importlib.reload(av_store)
    importlib.reload(market_data)
    importlib.reload(risk_report)

    rng = np.random.default_rng(11)

    def _prices(start: float, drift: float) -> pd.DataFrame:
        rets = rng.normal(drift, 0.01, N_DAYS)
        series = start * np.cumprod(1.0 + rets)
        return pd.DataFrame({"adj_close": series, "close": series}, index=DAYS)

    av_store.save_prices("ACWI", _prices(100.0, 0.0003))
    av_store.set_symbol_meta(
        "ACWI", "USD", "benchmark", ASOF, datetime(2024, 1, 8, 22, 0)
    )
    for ticker, currency in (("AAA", "USD"), ("BBB", "EUR")):
        av_store.save_av_mapping(ticker, ticker, currency)
        av_store.save_prices(ticker, _prices(50.0, 0.0004))
        av_store.set_symbol_meta(
            ticker, currency, "aktie", ASOF, datetime(2024, 1, 8, 22, 5)
        )
    av_store.save_prices(
        "FX:USDEUR",
        pd.DataFrame({"adj_close": pd.Series(0.9 + rng.normal(0, 0.002, N_DAYS).cumsum() * 0.01, index=DAYS)}),
    )
    av_store.save_prices(
        "MACRO:Y10",
        pd.DataFrame({"adj_close": pd.Series(3.5 + rng.normal(0, 0.03, N_DAYS).cumsum(), index=DAYS)}),
    )
    av_store.save_prices(
        "MACRO:WTI",
        pd.DataFrame({"adj_close": pd.Series(75.0 + rng.normal(0, 0.5, N_DAYS).cumsum(), index=DAYS)}),
    )
    return risk_report


WEIGHTS = {"AAA": 0.6, "BBB": 0.4}


def _scored() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ticker": "AAA",
                "sector": "Information Technology",
                "total_score": 71.2,
                "recommendation": "SELL",
                "sma_signal": "⚠ DEATH CROSS",
            },
            {
                "ticker": "BBB",
                "sector": "Financials",
                "total_score": 55.0,
                "recommendation": "HOLD",
                "sma_signal": "● Kurs > SMA-200",
            },
        ]
    )


def test_compute_risk_report_end_to_end(env):
    res = env.compute_risk_report(
        ["AAA", "BBB"], WEIGHTS, Settings(), _scored(), ASOF
    )

    assert res["mcte"] is not None
    assert res["expost"]["n_tage"] > 250
    assert not res["ranking"].empty
    assert set(res["ranking"]["ticker"]) == {"AAA", "BBB"}
    # Signale hängen am Ranking.
    row = res["ranking"].set_index("ticker").loc["AAA"]
    assert row["recommendation"] == "SELL"
    # Szenarien: alle Default-Fenster liegen vor dem Fixture-Zeitraum außer
    # keinem — GFC etc. haben keine Benchmark-Historie → nicht belastbar.
    assert len(res["szenarien"]) == len(Settings().risk_scenario_windows)
    assert not res["sektor_allokation"].empty
    assert not res["schocks"].empty


def test_compute_with_scenario_filter(env):
    res = env.compute_risk_report(
        ["AAA", "BBB"], WEIGHTS, Settings(), _scored(), ASOF, only=["COVID"]
    )
    assert [s.name for s in res["szenarien"]] == ["COVID"]
    assert res["unbekannte_szenarien"] == []


def test_markdown_report_structure_and_german_formats(env):
    res = env.compute_risk_report(
        ["AAA", "BBB"], WEIGHTS, Settings(), _scored(), ASOF
    )
    md = env.build_markdown_report(res)

    # Kopf: Stichtag deutsch formatiert + Disclaimer.
    assert f"Stichtag: {pd.Timestamp(ASOF).strftime('%d.%m.%Y')}" in md
    assert "Interne Analyse, keine Anlageberatung" in md
    # Alle sieben Sektionen vorhanden.
    for heading in (
        "## 1. Management Summary",
        "## 2. Tracking Error & Kennzahlen",
        "## 3. Risikobeiträge je Einzeltitel (MCTE)",
        "## 4. Aktive Sektorallokation",
        "## 5. Historische Szenarien",
        "## 6. Hypothetische Faktor-Schocks",
        "## 7. Datenqualität",
    ):
        assert heading in md
    # Dezimal-KOMMA statt Punkt in Prozentwerten.
    assert "," in md and " %" in md
    # Modell-Signale im MCTE-Ranking.
    assert "SELL" in md and "DEATH CROSS" in md
    # bp-Werte ganzzahlig mit Einheit.
    assert " bp" in md
    # Nicht belastbare Szenarien sind markiert (GFC liegt vor der Historie).
    assert "nicht belastbar" in md
    # EUR-Sicht ohne Hedging dokumentiert.
    assert "ohne Currency-Hedging" in md


def test_write_report_filename(env, tmp_path):
    res = env.compute_risk_report(["AAA", "BBB"], WEIGHTS, Settings(), None, ASOF)
    path = env.write_report(res, tmp_path / "reports")
    assert path.name == f"risiko_benchmark_{ASOF.isoformat()}.md"
    assert path.read_text(encoding="utf-8").startswith("# Risiko & Benchmark")


def test_report_without_universe_keeps_running(env):
    res = env.compute_risk_report(["AAA", "BBB"], WEIGHTS, Settings(), None, ASOF)
    md = env.build_markdown_report(res)
    assert "## 3." in md


# ── CLI ────────────────────────────────────────────────────────────────────


def _fake_state(monkeypatch, tools, tickers, weights):
    state = AppState()
    state.ms_portfolio = tickers
    state.ms_portfolio_weights = weights
    state.raw = pd.DataFrame()
    state.scored = _scored() if tickers else pd.DataFrame()
    monkeypatch.setattr(state, "load_from_db", lambda: True)
    monkeypatch.setattr(tools, "STATE", state)
    return state


def test_cli_report_writes_file(env, tmp_path, monkeypatch):
    from app.tools import risk_report as tools

    importlib.reload(tools)
    _fake_state(monkeypatch, tools, ["AAA", "BBB"], WEIGHTS)

    out = tmp_path / "out"
    rc = tools.main(
        ["report", "--asof", ASOF.isoformat(), "--out", str(out)]
    )
    assert rc == 0
    files = list(out.glob("risiko_benchmark_*.md"))
    assert len(files) == 1


def test_cli_exit_1_without_portfolio(env, monkeypatch, capsys):
    from app.tools import risk_report as tools

    importlib.reload(tools)
    _fake_state(monkeypatch, tools, [], {})

    assert tools.main(["report"]) == 1
    assert "Portfolio" in capsys.readouterr().err


def test_cli_exit_2_on_unknown_scenario(env, monkeypatch, capsys):
    from app.tools import risk_report as tools

    importlib.reload(tools)
    _fake_state(monkeypatch, tools, ["AAA"], {"AAA": 1.0})

    assert tools.main(["report", "--only", "Dotcom"]) == 2
    err = capsys.readouterr().err
    assert "Dotcom" in err and "COVID" in err


def test_cli_exit_2_on_bad_date(env, monkeypatch, capsys):
    from app.tools import risk_report as tools

    importlib.reload(tools)
    _fake_state(monkeypatch, tools, ["AAA"], {"AAA": 1.0})

    assert tools.main(["report", "--asof", "08.01.2024"]) == 2
    assert "YYYY-MM-DD" in capsys.readouterr().err


def test_cli_manual_mapping_saved(env, tmp_path, monkeypatch):
    from app.core import av_store
    from app.tools import risk_report as tools

    importlib.reload(tools)
    _fake_state(monkeypatch, tools, ["AAA", "BBB"], WEIGHTS)

    rc = tools.main(
        [
            "report",
            "--asof",
            ASOF.isoformat(),
            "--out",
            str(tmp_path / "out"),
            "--map",
            "SAP=SAP.DEX:EUR",
        ]
    )
    assert rc == 0
    mapping = av_store.load_av_mapping("SAP")
    assert mapping == {"av_symbol": "SAP.DEX", "currency": "EUR", "confirmed": True}


def test_benchmark_sector_weights_source():
    """Die aktive Sektorallokation folgt pc_benchmark_source: 'universe' =
    marktkapitalisierungsgewichtete Universums-Anteile, 'static' =
    ACWI-Dict aus den Einstellungen; ohne Universum Fallback auf static."""
    from app.core.config import Settings
    from app.core.risk_report import _benchmark_sector_weights

    scored = pd.DataFrame(
        {
            "uid": ["A", "B", "C"],
            "sector": ["Tech", "Tech", "Health"],
            "market_cap": [600.0, 200.0, 200.0],
        }
    )

    s = Settings()
    s.pc_benchmark_source = "universe"
    weights, quelle = _benchmark_sector_weights(s, scored)
    assert quelle == "universe"
    assert weights["Tech"] == pytest.approx(0.8)
    assert weights["Health"] == pytest.approx(0.2)

    # Ohne Universum → statische ACWI-Gewichte.
    weights_none, quelle_none = _benchmark_sector_weights(s, None)
    assert quelle_none == "static"
    assert weights_none == s.risk_benchmark_sector_weights

    s.pc_benchmark_source = "static"
    weights_static, quelle_static = _benchmark_sector_weights(s, scored)
    assert quelle_static == "static"
    assert weights_static == s.risk_benchmark_sector_weights


def test_v2_report_requires_composite_values():
    """Der Markdown-Report zeigt v2-Spalten nur, wenn scoring_version = v2
    UND das Ranking tatsächlich Composite-Werte trägt (join_signals legt
    die Spalten sonst leer an)."""
    from app.core.risk_report import _v2_report

    empty = pd.DataFrame({"composite_score": [pd.NA, pd.NA]})
    filled = pd.DataFrame({"composite_score": [61.5, pd.NA]})

    assert not _v2_report({"scoring_version": "v2", "ranking": empty})
    assert not _v2_report({"scoring_version": "v1", "ranking": filled})
    assert _v2_report({"scoring_version": "v2", "ranking": filled})
