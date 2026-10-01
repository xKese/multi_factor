"""Smoke-Tests der Seite „Bericht" (``app.pages.bericht``)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import pytest

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "koyfin_sample.csv"


@pytest.fixture(scope="module")
def page_module():
    dash.Dash(
        __name__,
        use_pages=True,
        pages_folder="",
        external_stylesheets=[dbc.themes.BOOTSTRAP],
        suppress_callback_exceptions=True,
    )
    from app.pages import bericht  # type: ignore[import-untyped]

    return bericht


def _walk(component):
    yield component
    children = getattr(component, "children", None)
    if children is None:
        return
    if not isinstance(children, (list, tuple)):
        children = [children]
    for child in children:
        if hasattr(child, "children") or hasattr(child, "_prop_names"):
            yield from _walk(child)


def _ids(component) -> set[str]:
    return {c.id for c in _walk(component) if isinstance(getattr(c, "id", None), str)}


def test_page_registered(page_module):
    assert any(p["path"] == "/bericht" and p["name"] == "Bericht" for p in dash.page_registry.values())


def test_layout_without_universe_shows_hint(page_module, monkeypatch):
    from app.core.state import STATE

    monkeypatch.setattr(STATE, "scored", pd.DataFrame(), raising=False)
    node = page_module.layout()
    assert "cr-pdf" not in _ids(node)
    assert any("Kein Universum" in str(getattr(c, "children", "")) for c in _walk(node))


def test_layout_with_universe_has_controls_and_preview(page_module, monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/page.db")
    from app.core import persistence, signal_events

    importlib.reload(persistence)
    signal_events.clear_cache()
    from app.core.data_loader import load_koyfin_csv
    from app.core.state import STATE

    monkeypatch.setattr(STATE, "ms_portfolios", [], raising=False)
    monkeypatch.setattr(STATE, "refresh_portfolios", lambda: None)
    STATE.set_raw(load_koyfin_csv(FIXTURE.read_bytes()))
    node = page_module.layout()
    ids = _ids(node)
    for cid in ("cr-portfolio", "cr-prev", "cr-month", "cr-sections", "cr-topn", "cr-author", "cr-preview", "cr-pdf", "cr-xlsx", "cr-pdf-download", "cr-xlsx-download", "cr-status", "cr-preview-body"):
        assert cid in ids, cid
    texts = [str(getattr(c, "children", "")) for c in _walk(node)]
    assert any("Key Findings" in t for t in texts)


def test_options_from_inputs(page_module):
    opts = page_module._options_from_inputs(3, "2026-03-06", "auto", ["sectors", "risk"], 50, " Kevin ")
    assert opts.portfolio_id == 3
    assert opts.prev_snapshot == date(2026, 3, 6) and opts.month_snapshot is None
    assert opts.include_sectors and opts.include_risk
    assert not opts.include_model_portfolio and not opts.include_parameters
    assert opts.top_n == 50 and opts.prepared_by == "Kevin"
    opts = page_module._options_from_inputs(None, "auto", "auto", None, "abc", None)
    assert opts.portfolio_id is None and opts.top_n == page_module.DEFAULT_TOP_N
    assert not opts.include_sectors


def test_callbacks_return_downloads(page_module, monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/cb.db")
    from app.core import persistence, signal_events

    importlib.reload(persistence)
    signal_events.clear_cache()
    from app.core import committee_report_pdf
    from app.core.data_loader import load_koyfin_csv
    from app.core.state import STATE

    monkeypatch.setattr(STATE, "ms_portfolios", [], raising=False)
    STATE.set_raw(load_koyfin_csv(FIXTURE.read_bytes()))
    monkeypatch.setattr(committee_report_pdf, "_run_weasyprint", lambda html, base_url: b"%PDF-stub")
    preview, status = page_module._render_preview(1, None, "auto", "auto", ["sectors"], 25, "")
    assert isinstance(preview, list) and status is None
    data, status = page_module._download_pdf(1, None, "auto", "auto", ["sectors"], 25, "")
    assert status is None and data["filename"].endswith(".pdf")
    data, status = page_module._download_xlsx(1, None, "auto", "auto", ["sectors"], 25, "")
    assert status is None and data["filename"].endswith(".xlsx")


def test_pdf_callback_falls_back_to_html(page_module, monkeypatch, tmp_path):
    import importlib

    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/fb.db")
    from app.core import persistence, signal_events

    importlib.reload(persistence)
    signal_events.clear_cache()
    from app.core import committee_report_pdf
    from app.core.data_loader import load_koyfin_csv
    from app.core.state import STATE

    monkeypatch.setattr(STATE, "ms_portfolios", [], raising=False)
    STATE.set_raw(load_koyfin_csv(FIXTURE.read_bytes()))

    def boom(html, base_url):
        raise committee_report_pdf.FactsheetRenderError("kein Worker")

    monkeypatch.setattr(committee_report_pdf, "_run_weasyprint", boom)
    data, status = page_module._download_pdf(1, None, "auto", "auto", [], 25, "")
    assert data["filename"].endswith(".html")
    assert status is not None
