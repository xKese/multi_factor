"""Smoke-Tests der M&S-Portfolio-Seite mit mehreren Portfolios."""

from __future__ import annotations

import pandas as pd
import pytest

import dash
import dash_bootstrap_components as dbc
from dash import no_update


@pytest.fixture(scope="module")
def page_module():
    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import portfolios  # type: ignore[import-untyped]

    return portfolios


def _ids(node, acc: set | None = None) -> set:
    acc = set() if acc is None else acc
    node_id = getattr(node, "id", None)
    if isinstance(node_id, str):
        acc.add(node_id)
    children = getattr(node, "children", None)
    if isinstance(children, (list, tuple)):
        for c in children:
            _ids(c, acc)
    elif children is not None and not isinstance(children, (str, int, float)):
        _ids(children, acc)
    return acc


def test_layout_contains_selector_name_and_delete(page_module, monkeypatch):
    from app.core.state import STATE

    monkeypatch.setattr(STATE, "refresh_portfolios", lambda: None)
    monkeypatch.setattr(
        STATE,
        "ms_portfolios",
        [{"id": 1, "name": "Depot A", "n_positions": 2}],
        raising=False,
    )
    monkeypatch.setattr(STATE, "active_portfolio_id", 1, raising=False)
    ids = _ids(page_module.layout())
    assert {"pf-main", "pf-select", "pf-upload", "pf-upload-name", "pf-delete",
            "pf-select-status", "pf-upload-status"} <= ids
    options = page_module._portfolio_options()
    assert options == [{"label": "Depot A · 2 Pos.", "value": 1}]


def test_dispatch_without_trigger_is_noop(page_module):
    out = page_module._on_portfolio_action(None, None, None, None, None)
    assert len(out) == 7
    assert all(o is no_update for o in out)


def test_upload_creates_named_portfolio_and_sets_active(page_module, monkeypatch):
    """Upload legt ein benanntes Portfolio an, setzt es aktiv und rendert neu."""
    import base64

    from app.core import persistence
    from app.core.state import STATE

    saved: dict = {}

    def _save(df, name, **kw):
        saved["name"] = name
        saved["tickers"] = list(df["ticker"])
        return 5, len(df)

    catalog = [{"id": 5, "name": "Depot Neu", "n_positions": 2}]
    monkeypatch.setattr(persistence, "save_ms_portfolio_named", _save)
    monkeypatch.setattr(
        persistence, "set_portfolio_selection", lambda key, pid: saved.setdefault("sel", (key, pid))
    )
    monkeypatch.setattr(persistence, "list_ms_portfolios", lambda: catalog)
    monkeypatch.setattr(STATE, "scored", pd.DataFrame(), raising=False)
    monkeypatch.setattr(STATE, "ms_portfolios", [], raising=False)

    contents = "data:text/csv;base64," + base64.b64encode(
        b"Ticker;Name\nAAA;Alpha\nBBB;Beta\n"
    ).decode()
    out = page_module._handle_upload(contents, "depot_neu.csv", "")
    assert saved["name"] == "depot_neu"
    assert saved["tickers"] == ["AAA", "BBB"]
    assert saved["sel"] == (persistence.SELECTION_ACTIVE, 5)
    assert STATE.active_portfolio_id == 5
    assert STATE.ms_portfolio == ["AAA", "BBB"]
    assert out[1] == [{"label": "Depot Neu · 2 Pos.", "value": 5}]
    assert out[2] == 5
    assert "„depot_neu“ angelegt" in out[3]
