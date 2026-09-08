"""Tests für mehrere hochgeladene M&S-Portfolios: Persistenz (Migration,
CRUD, Auswahl) und die State-Auflösung nach Portfolio-ID."""

from __future__ import annotations

import importlib
from datetime import date, datetime

import pandas as pd
import pytest
from sqlalchemy import text

from app.core import persistence
from app.core.state import AppState


def _fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    importlib.reload(persistence)
    return persistence


def _df(*tickers: str, weights: list[float] | None = None) -> pd.DataFrame:
    df = pd.DataFrame({"ticker": list(tickers), "name": [t.lower() for t in tickers]})
    if weights is not None:
        df["weight"] = weights
    return df


# ── Legacy-Migration ───────────────────────────────────────────────────────


def test_legacy_table_migrates_to_portfolio_1(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    engine = p.get_engine()
    with engine.begin() as conn:
        # Altes Ein-Portfolio-Schema ohne Gewichtsspalte.
        conn.execute(
            text(
                "CREATE TABLE ms_portfolio ("
                "position INTEGER PRIMARY KEY, ticker TEXT NOT NULL, "
                "name TEXT, imported_at TIMESTAMP NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO ms_portfolio (position, ticker, name, imported_at) "
                "VALUES (0, 'BBB', 'Beta', '2026-07-01 10:00:00'), "
                "(1, 'AAA', 'Alpha', '2026-07-01 10:00:00')"
            )
        )

    catalog = p.list_ms_portfolios()
    assert len(catalog) == 1
    assert catalog[0]["id"] == 1
    assert catalog[0]["name"] == p.LEGACY_PORTFOLIO_NAME
    assert catalog[0]["n_positions"] == 2
    assert catalog[0]["imported_at"] == datetime(2026, 7, 1, 10, 0, 0)
    assert p.get_active_portfolio_id() == 1

    loaded = p.load_ms_portfolio()
    assert list(loaded["ticker"]) == ["BBB", "AAA"]  # Reihenfolge erhalten
    assert loaded["weight"].isna().all()

    # Legacy-Tabelle geleert, zweiter Aufruf idempotent.
    with engine.begin() as conn:
        n_legacy = conn.execute(text("SELECT COUNT(*) FROM ms_portfolio")).scalar()
    assert n_legacy == 0
    assert len(p.list_ms_portfolios()) == 1

    # Nach dem Löschen aller Portfolios keine Re-Migration.
    p.delete_ms_portfolio(1)
    assert p.list_ms_portfolios() == []
    assert p.get_active_portfolio_id() is None
    assert p.load_ms_portfolio() is None


# ── CRUD & Auswahl ─────────────────────────────────────────────────────────


def test_multi_portfolio_crud_and_selection(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)

    pid_a, n_a = p.save_ms_portfolio_named(
        _df("AAA", "BBB", weights=[0.6, 0.4]), "Depot A",
        source_filename="a.csv", imported_at=datetime(2026, 9, 1, 8, 0),
    )
    pid_b, n_b = p.save_ms_portfolio_named(_df("CCC"), "Depot B")
    assert (pid_a, n_a, pid_b, n_b) == (1, 2, 2, 1)

    catalog = p.list_ms_portfolios()
    assert [(c["id"], c["name"], c["n_positions"]) for c in catalog] == [
        (1, "Depot A", 2),
        (2, "Depot B", 1),
    ]
    assert catalog[0]["source_filename"] == "a.csv"
    assert catalog[0]["imported_at"] == datetime(2026, 9, 1, 8, 0)

    # Ohne explizite Auswahl ist das Portfolio mit der kleinsten ID aktiv.
    assert p.get_active_portfolio_id() == 1

    # Gleicher Name in anderer Schreibweise ersetzt die Positionen, ID bleibt.
    pid_a2, n_a2 = p.save_ms_portfolio_named(_df("DDD", "EEE", "FFF"), "depot a")
    assert (pid_a2, n_a2) == (1, 3)
    loaded_a = p.load_ms_portfolio_by_id(1)
    assert list(loaded_a["ticker"]) == ["DDD", "EEE", "FFF"]
    assert "imported_at" in loaded_a.columns
    assert p.list_ms_portfolios()[0]["name"] == "depot a"

    # Umbenennen auf einen bereits vergebenen Namen wird abgelehnt.
    with pytest.raises(ValueError):
        p.save_ms_portfolio_named(_df("XXX"), "Depot B", portfolio_id=1)

    # Auswahl persistieren (active / model_source unabhängig).
    p.set_portfolio_selection(p.SELECTION_ACTIVE, 2)
    p.set_portfolio_selection(p.SELECTION_MODEL_SOURCE, 1)
    assert p.get_active_portfolio_id() == 2
    assert p.get_portfolio_selection(p.SELECTION_MODEL_SOURCE) == 1
    assert list(p.load_ms_portfolio()["ticker"]) == ["CCC"]

    # Wrapper schreibt ins aktive Portfolio (id 2).
    assert p.save_ms_portfolio(_df("GGG", "HHH")) == 2
    assert list(p.load_ms_portfolio_by_id(2)["ticker"]) == ["GGG", "HHH"]
    assert len(p.list_ms_portfolios()) == 2

    # Suche per ID oder Name (CLI).
    assert p.find_ms_portfolio("2")["name"] == "Depot B"
    assert p.find_ms_portfolio("DEPOT A")["id"] == 1
    assert p.find_ms_portfolio("gibt es nicht") is None

    # Löschen entfernt Selektionen; aktiv fällt auf das verbleibende zurück.
    p.delete_ms_portfolio(2)
    assert p.get_portfolio_selection(p.SELECTION_ACTIVE) is None
    assert p.get_active_portfolio_id() == 1
    assert p.get_portfolio_selection(p.SELECTION_MODEL_SOURCE) == 1
    assert p.load_ms_portfolio_by_id(2) is None
    assert p.load_ms_portfolio_by_id(None) is None


def test_save_named_rejects_empty(tmp_path, monkeypatch):
    p = _fresh_db(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        p.save_ms_portfolio_named(_df("AAA"), "   ")
    with pytest.raises(ValueError):
        p.save_ms_portfolio_named(pd.DataFrame({"ticker": []}), "Leer")
    # Fehlgeschlagene Speicherung legt keinen Kopf an.
    assert p.list_ms_portfolios() == []


def test_multi_portfolio_fail_open_without_engine(monkeypatch):
    monkeypatch.setattr(persistence, "get_engine", lambda: None)
    assert persistence.list_ms_portfolios() == []
    assert persistence.get_portfolio_selection(persistence.SELECTION_ACTIVE) is None
    assert persistence.get_active_portfolio_id() is None
    assert persistence.load_ms_portfolio_by_id(1) is None
    assert persistence.find_ms_portfolio("1") is None
    with pytest.raises(RuntimeError):
        persistence.save_ms_portfolio_named(_df("AAA"), "X")
    with pytest.raises(RuntimeError):
        persistence.set_portfolio_selection(persistence.SELECTION_ACTIVE, 1)
    with pytest.raises(RuntimeError):
        persistence.delete_ms_portfolio(1)


def test_model_portfolio_meta_source_columns(tmp_path, monkeypatch):
    """Bestandsportfolio-Herkunft wird in model_portfolio_meta gespeichert;
    eine Bestands-Meta-Tabelle ohne die Spalten wird nachgerüstet."""
    p = _fresh_db(tmp_path, monkeypatch)
    engine = p.get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE model_portfolio_meta ("
                "snapshot_date DATE PRIMARY KEY, rebalance_mode TEXT, "
                "n_titles INTEGER, te_ex_ante DOUBLE PRECISION, "
                "te_coverage DOUBLE PRECISION, turnover_oneway DOUBLE PRECISION, "
                "n_trades INTEGER, n_deferred INTEGER, settings_hash TEXT, "
                "diagnostics TEXT, updated_at TIMESTAMP NOT NULL "
                "DEFAULT CURRENT_TIMESTAMP)"
            )
        )
    snap = date(2026, 9, 7)
    portfolio = pd.DataFrame(
        {
            "uid": ["AAA"], "composite_z": [1.0], "composite_pct": [0.9],
            "zone_v2": ["KANDIDAT"], "weight_model": [1.0],
            "weight_effective": [1.0], "cte": [0.05], "action": ["KAUF"],
            "reason": ["zone_KANDIDAT"], "rebalance_mode": ["full"],
            "override_id": [None],
        }
    )
    meta = {
        "rebalance_mode": "full", "n_titles": 1, "te_ex_ante": 0.05,
        "te_coverage": 1.0, "turnover_oneway": 0.5, "n_trades": 1,
        "n_deferred": 0, "settings_hash": "x", "diagnostics": "[]",
        "source_portfolio_id": 2, "source_portfolio_name": "Depot B",
    }
    p.save_model_portfolio(portfolio, meta, snap)
    loaded = p.load_model_portfolio_meta(snap)
    assert loaded["source_portfolio_id"] == 2
    assert loaded["source_portfolio_name"] == "Depot B"

    # Ohne Angabe bleiben die Felder leer (ältere Aufrufer).
    meta.pop("source_portfolio_id")
    meta.pop("source_portfolio_name")
    p.save_model_portfolio(portfolio, meta, snap)
    loaded = p.load_model_portfolio_meta(snap)
    assert loaded["source_portfolio_id"] is None
    assert loaded["source_portfolio_name"] is None


# ── State ──────────────────────────────────────────────────────────────────


def _scored() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "ticker": ["AAA", "BBB", "CCC"],
            "uid": ["AAA", "BBB", "CCC"],
            "name": ["Alpha", "Beta", "Gamma"],
        }
    )


def test_resolve_non_active_portfolio_by_id(monkeypatch):
    state = AppState()
    state.scored = _scored()
    state.set_ms_portfolio(
        pd.DataFrame({"ticker": ["AAA", "BBB"], "weight": [0.7, 0.3]}),
        portfolio_id=1,
    )
    other = pd.DataFrame(
        {"ticker": ["CCC"], "name": ["Gamma"], "weight": [1.0],
         "imported_at": ["2026-09-01"]}
    )
    monkeypatch.setattr(
        persistence, "load_ms_portfolio_by_id",
        lambda pid: other if pid == 7 else None,
    )

    assert state.portfolio_weights() == pytest.approx({"AAA": 0.7, "BBB": 0.3})
    assert state.portfolio_weights(portfolio_id=1) == pytest.approx(
        {"AAA": 0.7, "BBB": 0.3}
    )
    assert state.portfolio_weights(portfolio_id=7) == pytest.approx({"CCC": 1.0})
    resolved = state.resolve_portfolio(portfolio_id=7)
    assert list(resolved["uid"]) == ["CCC"]
    assert list(resolved["status"]) == ["ok"]
    # Unbekannte ID → leer, aktive Felder unberührt.
    assert state.portfolio_weights(portfolio_id=99) == {}
    assert state.ms_portfolio == ["AAA", "BBB"]
    assert state.active_portfolio_id == 1


def test_model_source_falls_back_to_active(monkeypatch):
    state = AppState()
    state.ms_portfolios = [
        {"id": 1, "name": "Depot A"}, {"id": 2, "name": "Depot B"},
    ]
    state.active_portfolio_id = 1

    monkeypatch.setattr(persistence, "get_portfolio_selection", lambda key: 2)
    assert state.model_source_portfolio_id() == 2
    # Selektion zeigt auf ein nicht (mehr) vorhandenes Portfolio.
    monkeypatch.setattr(persistence, "get_portfolio_selection", lambda key: 9)
    assert state.model_source_portfolio_id() == 1
    monkeypatch.setattr(persistence, "get_portfolio_selection", lambda key: None)
    assert state.model_source_portfolio_id() == 1
    assert state.portfolio_name(2) == "Depot B"
    assert state.portfolio_name(None) == ""
    assert state.active_portfolio_name == "Depot A"


def test_reset_ms_portfolio():
    state = AppState()
    default = list(state.ms_portfolio)
    state.set_ms_portfolio(
        pd.DataFrame({"ticker": ["AAA"], "name": ["Alpha"]}),
        imported_at="2026-09-01", portfolio_id=4,
    )
    assert state.active_portfolio_id == 4
    state.reset_ms_portfolio()
    assert state.ms_portfolio == default
    assert state.ms_portfolio_names == {}
    assert state.ms_portfolio_entries.empty
    assert state.ms_portfolio_imported_at is None
    assert state.active_portfolio_id is None
    assert state.active_portfolio_name is None
