"""Tests für den PDF-Export der Agenten-Berichte (#5a/#5b)."""

from __future__ import annotations

import io

from app.core.pdf_export import (
    AGENT_COUNT,
    PDF_AUTHOR,
    build_full_context,
    build_section_context,
    render_full_pdf,
    render_section_pdf,
    sanitize_filename,
)


MD_REPORT = (
    "Der Kurs notiert 12 % über der SMA-200. Das Momentum liegt im obersten "
    "Quintil.\n\n## Momentum\n\nKein Überhitzungssignal.\n\n"
    "| Kennzahl | Wert |\n|---|---|\n| RSI | 58 |\n\n- Punkt eins\n- Punkt zwei"
)

FACTOR_CONTEXT = {
    "total_score": 78.4,
    "piotroski": 8.0,
    "altman_z": 3.1,
    "classification": "B+ - Sehr Gut",
    "recommendation": "BUY",
    "factor_scores": {
        "value": 61.0,
        "quality": 82.5,
        "growth": 74.0,
        "momentum": 79.3,
        "lowvol": 55.1,
    },
    "signals": {
        "sma_signal": "Über SMA-200",
        "trend_phase": "Aufwärtstrend",
        "mom_12_1": 0.2412,
        "dist_52w_high": -0.043,
    },
    "identity": {"name": "SAP SE"},
}


def _analysis(**overrides) -> dict:
    base = {
        "ticker": "SAP",
        "run_id": "run-1",
        "in_universe": 1,
        "rating": "Overweight",
        "provider": "Anthropic",
        "created_at": "2026-07-21 12:32:00",
        "executive_summary": None,
        "factor_context": FACTOR_CONTEXT,
        "reports": {
            "market_report": MD_REPORT,
            "sentiment_report": "Verhalten positiv. Kein Euphorie-Signal.",
            "fundamentals_report": MD_REPORT,
            "investment_plan": "",  # leer → wird übersprungen
            "final_trade_decision": "Einstimmig moderate Übergewichtung. "
            "Die Fundamentaldaten stützen das Quant-Bild.",
        },
    }
    base.update(overrides)
    return base


def test_build_section_context_keys():
    ctx = build_section_context(_analysis(), "market_report", None)
    for key in (
        "company",
        "section_title",
        "meta_line",
        "lead",
        "body_html",
        "sidebar",
        "pdf_title",
        "filename",
    ):
        assert key in ctx, f"missing context key: {key}"
    assert ctx["company"] == "SAP SE"
    assert ctx["section_title"] == "Marktanalyse"
    assert ctx["lead"]
    assert "<h" in str(ctx["body_html"])
    assert "#" not in str(ctx["body_html"]).replace("&#", "")
    assert ctx["sidebar"]["rating"]["note"] == f"Konsens aus {AGENT_COUNT} Agenten"
    assert ctx["sidebar"]["quant"]["class_line"] == "B+ · SEHR GUT"
    assert ctx["sidebar"]["delta"]["text"] == "Prior bestätigt"
    assert ctx["filename"] == "SAP_Marktanalyse_2026-07-21.pdf"


def test_section_metrics_only_existing():
    fc = {"total_score": 50.0, "classification": "C - Durchschnitt"}
    ctx = build_section_context(
        _analysis(factor_context=fc), "market_report", None
    )
    # Ohne signals/piotroski und ohne scored-Zeile bleiben keine Kennzahlen.
    assert ctx["sidebar"]["metrics"] == []

    ctx2 = build_section_context(
        _analysis(factor_context=None), "market_report", None
    )
    assert ctx2["sidebar"]["quant"] is None
    assert ctx2["sidebar"]["delta"] is None


def test_section_unknown_key_raises():
    import pytest

    with pytest.raises(ValueError):
        build_section_context(_analysis(), "investment_plan", None)


def test_full_context_numbering_and_toc():
    ctx = build_full_context(_analysis(), None)
    # investment_plan ist leer → 4 Sektionen, fortlaufend nummeriert
    nums = [s["num"] for s in ctx["sections"]]
    assert nums == ["01", "02", "03", "04"]
    assert ctx["scope_line"] == f"4 Berichte · {AGENT_COUNT} Agenten"
    keys = [s["key"] for s in ctx["sections"]]
    assert "investment_plan" not in keys
    assert ctx["date_long"] == "21. Juli 2026"
    assert ctx["filename"] == "SAP_Agenten-Tiefenanalyse_2026-07-21.pdf"
    assert ctx["summary"]


def test_markdown_escapes_raw_html():
    analysis = _analysis()
    analysis["reports"]["market_report"] = (
        "Text mit <script>alert(1)</script> Einschub."
    )
    ctx = build_section_context(analysis, "market_report", None)
    assert "<script>" not in str(ctx["body_html"])


def test_filename_sanitization():
    assert (
        sanitize_filename("SAP.DE", "Risiko & Entscheidung", "2026-07-21")
        == "SAP.DE_Risiko_und_Entscheidung_2026-07-21.pdf"
    )
    assert (
        sanitize_filename("MÜV2", "Größenprüfung", "2026-01-02")
        == "MUeV2_Groessenpruefung_2026-01-02.pdf"
    )


def test_render_section_pdf_smoke():
    pdf, filename = render_section_pdf(_analysis(), "market_report", None)
    assert pdf.startswith(b"%PDF-")
    assert len(pdf) > 5000
    assert filename == "SAP_Marktanalyse_2026-07-21.pdf"


def test_render_full_pdf_smoke():
    pdf, filename = render_full_pdf(_analysis(), None)
    assert pdf.startswith(b"%PDF-")
    assert filename == "SAP_Agenten-Tiefenanalyse_2026-07-21.pdf"
    try:
        import pypdf
    except ImportError:
        return
    reader = pypdf.PdfReader(io.BytesIO(pdf))
    # Deckblatt + Überblick + mind. eine Berichtsseite
    assert len(reader.pages) >= 3
    assert reader.metadata.title == "SAP SE · Agenten-Tiefenanalyse"
    assert reader.metadata.author == PDF_AUTHOR


def test_render_without_factor_context():
    adhoc = _analysis(factor_context=None, ticker="ADHOC")
    pdf, _ = render_full_pdf(adhoc, None)
    assert pdf.startswith(b"%PDF-")
