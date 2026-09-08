"""Smoke-Test der Modellportfolio-Seite (Spec 11.1)."""

from __future__ import annotations

import pytest

import dash
import dash_bootstrap_components as dbc


@pytest.fixture(scope="module")
def page_module():
    """Dash-App initialisieren, damit ``register_page`` beim Import der
    Seite nicht raised."""
    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import modellportfolio  # type: ignore[import-untyped]

    return modellportfolio


def test_layout_renders(page_module):
    node = page_module.layout()
    assert node is not None


def test_page_registered(page_module):
    assert any(
        p["path"] == "/modellportfolio" for p in dash.page_registry.values()
    )


def test_run_without_universe(page_module, monkeypatch):
    """Ohne geladenes Universum liefert der Render-Callback einen Hinweis."""
    import pandas as pd

    from app.core.state import STATE

    monkeypatch.setattr(STATE, "scored", pd.DataFrame(), raising=False)
    content, status = page_module._run(0, 0, "live", None, "auto")
    assert content is not None
    assert status == ""


def test_controls_offer_source_portfolio_selector(page_module, monkeypatch):
    """Der Bestandsportfolio-Selektor listet die hochgeladenen Portfolios
    und ist mit der gespeicherten Auswahl vorbelegt."""
    from app.core.state import STATE

    catalog = [
        {"id": 1, "name": "Depot A", "n_positions": 3},
        {"id": 2, "name": "Depot B", "n_positions": 5},
    ]
    monkeypatch.setattr(STATE, "refresh_portfolios", lambda: None)
    monkeypatch.setattr(STATE, "ms_portfolios", catalog, raising=False)
    monkeypatch.setattr(STATE, "model_source_portfolio_id", lambda: 2)

    controls = page_module._controls()
    dropdowns = {
        c.children.id: c.children
        for c in controls.children
        if hasattr(c, "children") and hasattr(c.children, "id")
    }
    source = dropdowns["mp-source"]
    assert [o["value"] for o in source.options] == [1, 2]
    assert "Depot B" in source.options[1]["label"]
    assert source.value == 2
