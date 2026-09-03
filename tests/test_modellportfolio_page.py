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
    content, status = page_module._run(0, 0, "live", "auto")
    assert content is not None
    assert status == ""
