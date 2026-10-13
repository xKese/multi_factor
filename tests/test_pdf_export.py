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


FACTOR_CONTEXT_V2 = {
    "scoring_version": "v2",
    "piotroski": 8.0,
    "altman_z": 3.1,
    "composite_v2": {
        "score": 83.4,
        "pct": 0.834,
        "classification": "B+",
        "zone": "KANDIDAT",
        "data_coverage": 0.95,
        "factors": {
            "value": {"z": 0.4, "coverage": 1.0},
            "quality": {"z": 1.2, "coverage": 0.75},
            "momentum": {"z": -0.35, "coverage": 1.0},
            "investment": {"coverage": 0.0},
        },
    },
    "signals": {"mom_12_1": 0.2412},
    "identity": {"name": "SAP SE"},
}


def test_section_context_with_composite_v2():
    ctx = build_section_context(
        _analysis(factor_context=FACTOR_CONTEXT_V2), "fundamentals_report", None
    )
    quant = ctx["sidebar"]["quant"]
    assert quant["score"] == "83,4"
    assert quant["class_line"] == "B+"
    assert quant["v2_line"].startswith("Composite v2 · Zone KANDIDAT")
    assert "95 %" in quant["v2_line"]
    # Zone KANDIDAT ↔ Overweight → Prior bestätigt.
    assert ctx["sidebar"]["delta"]["text"] == "Prior bestätigt"
    labels = {m["label"]: m for m in ctx["sidebar"]["metrics"]}
    # v1-Scores fehlen im v2-Kontext und entfallen; Faktor-Z erscheinen.
    assert "Score Qualität" not in labels
    assert labels["Z Qualität"]["value"] == "1,20"
    assert labels["Z Qualität"]["tone"] == "pos"
    assert labels["Piotroski"]["value"] == "8 / 9"

    ctx_full = build_full_context(_analysis(factor_context=FACTOR_CONTEXT_V2), None)
    final = next(s for s in ctx_full["sections"] if s["key"] == "final_trade_decision")
    z_labels = [m["label"] for m in final["metrics"]]
    assert z_labels == ["Z Value", "Z Qualität", "Z Momentum"]


def test_quant_block_v1_context_adds_current_v2_line():
    from app.core.pdf_export import _quant_block

    row = {"composite_score": 71.2, "classification_v2": "B", "zone_v2": "HALTEN"}
    block = _quant_block(FACTOR_CONTEXT, row)
    assert block["score"] == "78,4"
    assert block["v2_line"] == "aktuell: Composite v2 71,2 · B · HALTEN"
    assert _quant_block(FACTOR_CONTEXT, None)["v2_line"] is None


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
