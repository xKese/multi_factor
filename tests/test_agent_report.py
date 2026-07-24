"""Tests für die präsentationalen Helfer der Agenten-Report-Bausteine."""

from __future__ import annotations

from app.ui import agent_report as ar


def test_phase_assignment():
    agents = [
        "Market Analyst",
        "Sentiment Analyst",
        "News Analyst",
        "Fundamentals Analyst",
        "Bull Researcher",
        "Bear Researcher",
        "Research Manager",
        "Trader",
        "Risky Analyst",
        "Safe Analyst",
        "Neutral Analyst",
        "Portfolio Manager",
    ]
    phases = ar.assign_phases(agents)
    assert phases[0] == agents[:4]
    assert phases[1] == ["Bull Researcher", "Bear Researcher", "Research Manager"]
    assert phases[2] == ["Trader", "Risky Analyst", "Safe Analyst", "Neutral Analyst"]
    assert phases[3] == ["Portfolio Manager"]
    # Unbekannte Agentennamen landen in Phase 1 (Analysten).
    assert ar.phase_of("Quant Wizard") == 0


def test_progress_stats():
    job = {
        "agents": ["Market Analyst", "News Analyst", "Bull Researcher"],
        "agent_states": {
            "Market Analyst": "completed",
            "News Analyst": "completed",
            "Bull Researcher": "in_progress",
        },
    }
    stats = ar.progress_stats(job)
    assert stats["done"] == 2
    assert stats["total"] == 3
    assert stats["active"] == "Bull Researcher"
    assert stats["phase"] == 1  # Debatte-Phase aktiv


def test_rating_short_and_tone():
    assert ar.rating_short("Overweight") == "OW"
    assert ar.rating_short("Sell") == "S"
    assert ar.rating_short(None) == "–"
    assert ar.rating_tone("Buy") == "up"
    assert ar.rating_tone("Hold") == "warn"
    assert ar.rating_tone("Underweight") == "down"
    assert ar.rating_tone("Unfug") is None


def test_delta_quant_classification():
    fc_buy = {"recommendation": "BUY"}
    fc_sell = {"recommendation": "SELL"}
    assert ar.delta_quant("Overweight", fc_buy) == ("bestätigt", "up")
    assert ar.delta_quant("Buy", {"recommendation": "STRONG BUY"}) == ("bestätigt", "up")
    assert ar.delta_quant("Hold", fc_buy) == ("vorsichtiger", "warn")
    assert ar.delta_quant("Sell", fc_buy) == ("widerspricht", "down")
    assert ar.delta_quant("Underweight", fc_sell) == ("bestätigt", "up")
    # Ohne Quant-Kontext (Ad-hoc) keine Einstufung.
    assert ar.delta_quant("Buy", None) is None
    assert ar.delta_quant("Buy", {"recommendation": "Filter nicht bestanden"}) is None


def test_first_sentences_strips_markdown():
    text = "## Rating\n**Kurs** liegt 12 % über SMA-200. RSI neutral. Drittes Detail."
    teaser = ar.first_sentences(text, n=2)
    assert "Kurs liegt 12 % über SMA-200." in teaser
    assert "RSI neutral." in teaser
    assert "Drittes Detail" not in teaser
    assert "#" not in teaser and "*" not in teaser
    assert ar.first_sentences(None) == ""


def test_result_view_renders_hero_and_cards():
    analysis = {
        "rating": "Overweight",
        "provider": "anthropic",
        "created_at": "2026-07-21 14:32:00",
        "factor_context": {
            "total_score": 78.4,
            "classification": "B+ · Sehr Gut",
            "recommendation": "BUY",
        },
        "reports": {
            "market_report": "Kurs über SMA-200. Momentum stark.",
            "final_trade_decision": "**Rating**: Overweight. Fundamentaldaten stützen.",
        },
    }
    view = str(ar.result_view(analysis, current_score=79.1))
    assert "ms-agent-hero" in view
    assert "Overweight" in view
    assert "78,4" in view
    assert "Agenten bestätigen das Vorab-Rating" in view
    assert "Score zum Analysezeitpunkt" in view
    assert "Marktanalyse" in view and "Vollständig lesen" in view


def test_modal_sections_filters_and_orders():
    reports = {
        "news_report": "News-Inhalt.",
        "market_report": "Markt-Inhalt.",
        "bull_history": "   ",  # leer → gefiltert
    }
    sections = ar.modal_sections(reports)
    assert [k for k, _, _ in sections] == ["market_report", "news_report"]
    assert sections[0][1] == "Marktanalyse"
    assert ar.modal_sections(None) == []


def test_read_modal_content_head_and_nav():
    analysis = {
        "ticker": "SAP",
        "reports": {
            "market_report": "Markt.",
            "news_report": "News.",
            "final_trade_decision": "Entscheidung.",
        },
    }
    head, body, foot = ar._read_modal_content(analysis, "news_report")
    head_s, foot_s = str(head), str(foot)
    assert "SAP · Bericht 2 von 3 · News Analyst" in head_s
    assert "'News'" in head_s  # Serif-Titel
    assert "‹ Marktanalyse" in foot_s
    assert "Risiko & Entscheidung ›" in foot_s
    assert foot_s.count("ms-agent-read-dot") >= 3
    assert foot_s.count("is-active") == 1
    assert "News." in str(body)

    # Am Listenanfang ist der Zurück-Link unsichtbar.
    _, _, foot_first = ar._read_modal_content(analysis, "market_report")
    assert "visibility" in str(foot_first)


def test_shifted_key_bounds():
    reports = {"market_report": "a", "news_report": "b"}
    assert ar._shifted_key(reports, "market_report", 1) == "news_report"
    assert ar._shifted_key(reports, "market_report", -1) == "market_report"
    assert ar._shifted_key(reports, "news_report", 1) == "news_report"
    assert ar._shifted_key(reports, "unbekannt", 1) == "market_report"
    assert ar._shifted_key({}, "x", 1) == "x"


def test_result_view_links_open_modal():
    analysis = {
        "ticker": "SAP",
        "rating": "Buy",
        "reports": {"market_report": "Inhalt."},
    }
    view = str(ar.result_view(analysis))
    assert "agent-read" in view  # Pattern-ID statt Inline-Collapse
    assert "Vollständig lesen" in view
    assert "Details" not in view


def test_progress_checklist_renders_pipeline():
    job = {
        "agents_ticker": "SAP.DE",
        "agents": ["Market Analyst", "Bull Researcher", "Portfolio Manager"],
        "agent_states": {
            "Market Analyst": "completed",
            "Bull Researcher": "in_progress",
        },
        "started_at": 1_753_300_000.0,
    }
    view = str(ar.progress_checklist(job))
    assert "ms-agent-pipe" in view
    assert "Tiefenanalyse läuft · SAP.DE" in view
    assert "Bull Researcher arbeitet" in view
    assert "1 · Analysten" in view and "4 · Entscheidung" in view
