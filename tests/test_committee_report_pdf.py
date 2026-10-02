"""Tests des PDF-/HTML-Renderers (``app.core.committee_report_pdf``)."""

from __future__ import annotations

import pytest

from app.core import committee_report_pdf as pdfmod
from tests.test_committee_report import env, report, scored_universe  # noqa: F401


def test_pdf_context_is_formatted_and_truncated(report):  # noqa: F811
    ctx = pdfmod.build_pdf_context(report)
    assert ctx["title"] == "Investment-Komitee-Bericht"
    assert ctx["portfolio_name"] == "Testdepot"
    assert ctx["asof"] == "10.04.2026" and "April" in ctx["asof_long"]
    assert ctx["comparison_line"].startswith("Vorimport 03.04.2026")
    assert ctx["sections"]["model_portfolio"] and not ctx["sections"]["risk"]
    sell = ctx["universe"]["sell_zone"]
    assert sell["truncated"] and sell["shown"] == pdfmod.PDF_MAX_ROWS
    assert "Excel-Anhang" in sell["note"]
    # Alle Zellen sind Strings mit deutschem Dezimalkomma.
    first = ctx["universe"]["top_candidates"]["rows"][0]
    assert all(isinstance(c["text"], str) for c in first)
    score_cell = first[3]["text"]
    assert "." not in score_cell and ("," in score_cell or score_cell.isdigit())
    assert ctx["key_findings"] and all(f["icon"] for f in ctx["key_findings"])
    assert ctx["kpis"] and ctx["kpis"][0]["label"] == "Universum"


def test_html_contains_findings_and_sections(report):  # noqa: F811
    html = pdfmod.render_committee_html(report)
    assert "Testdepot" in html
    assert report["key_findings"][0]["text"][:40] in html
    for heading in ("Key Findings", "Investment-Universum", "Veränderungen", "Sektoren", "Modellportfolio", "Datenqualität", "Modellparameter"):
        assert heading in html
    assert 'href="committee.css"' in html


def test_pdf_pipeline_with_mocked_worker(report, monkeypatch):  # noqa: F811
    captured = {}

    def fake(html, base_url):
        captured["html"] = html
        captured["base_url"] = base_url
        return b"%PDF-stub"

    monkeypatch.setattr(pdfmod, "_run_weasyprint", fake)
    out = pdfmod.render_committee_pdf(report)
    assert out == b"%PDF-stub"
    assert captured["base_url"].endswith("/") and "Testdepot" in captured["html"]
    assert pdfmod.committee_filename(report, "pdf") == "Investment-Komitee-Bericht_Testdepot_2026-04-10.pdf"
    assert pdfmod.committee_filename(report, ".xlsx").endswith(".xlsx")


def test_fallback_html_is_standalone(report):  # noqa: F811
    html = pdfmod.render_committee_fallback_html(report).decode("utf-8")
    assert "<style>" in html and 'href="committee.css"' not in html
    assert "@page" not in html and "running(" not in html and "@font-face" not in html
    assert "Testdepot" in html


def test_real_weasyprint_render(report):  # noqa: F811
    """Echter Render über den WeasyPrint-Worker (wie in der App). Kein
    direkter ``import weasyprint`` im Testprozess — der wirft ohne
    pango/cairo auf dem Linker-Pfad einen ``OSError``, während der Worker
    die Nix-Bibliothekspfade selbst setzt (``factsheet_pdf._start_worker``)."""
    try:
        pdf = pdfmod.render_committee_pdf(report)
    except pdfmod.FactsheetRenderError as exc:  # Worker/Libs nicht verfügbar
        pytest.skip(f"WeasyPrint nicht lauffähig: {exc}")
    assert pdf.startswith(b"%PDF-")
    try:
        from pypdf import PdfReader
    except ImportError:
        return
    import io

    assert len(PdfReader(io.BytesIO(pdf)).pages) >= 6
