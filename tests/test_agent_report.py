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


def test_flip_callback_is_registered_on_the_right_function():
    """Regression: Der @callback-Dekorator des Blätter-Callbacks muss auf
    ``_flip_read_modal`` sitzen. Nach dem #16-Commit dekorierte er versehent-
    lich die davor eingefügte Hilfsfunktion ``_flip_step`` — Dash registrierte
    die falsche Funktion, jeder Klick warf SchemaTypeValidationError und das
    Umblättern war komplett tot."""
    from dash._callback import GLOBAL_CALLBACK_MAP

    # Der Blätter-Callback nutzt allow_duplicate-Outputs (Key mit @hash);
    # der Öffnen-Callback enthält -foot ebenfalls, aber zusätzlich is_open.
    entry = next(
        (
            v
            for k, v in GLOBAL_CALLBACK_MAP.items()
            if "ms-agent-read-foot.children@" in k and "is_open" not in k
        ),
        None,
    )
    assert entry is not None, "Blätter-Callback nicht registriert"
    assert entry["callback"].__name__ == "_flip_read_modal"

    # Und die Hilfsfunktion ist eine reine Funktion geblieben (kein Callback).
    assert ar._flip_step("ms-agent-read-next", 0, 1) == 1


def test_flip_step_requires_real_click():
    """Regression: Insertion-Fire der frisch gerenderten Nav-Buttons (n_clicks
    0/None) darf NICHT blättern — sonst öffnet „Vollständig lesen" immer den
    vorherigen Bericht (Bear-These → Bull-These)."""
    assert ar._flip_step("ms-agent-read-prev", 0, 0) is None
    assert ar._flip_step("ms-agent-read-prev", None, None) is None
    assert ar._flip_step("ms-agent-read-next", 0, None) is None
    assert ar._flip_step("ms-agent-read-prev", 1, 0) == -1
    assert ar._flip_step("ms-agent-read-next", 0, 2) == 1
    assert ar._flip_step("irgendwas", 1, 1) is None


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


def test_fmt_local_epoch_converts_to_berlin_time():
    """Regression: ``started_at`` (time.time()-Epoch) wurde als UTC-Wandzeit
    angezeigt — auf dem Replit-Server (UTC) stand dort „12:45" statt 14:45."""
    import calendar

    # 24.07.2026 12:45 UTC → 14:45 in Europe/Berlin (CEST, UTC+2).
    july = calendar.timegm((2026, 7, 24, 12, 45, 0))
    assert ar.fmt_local_epoch(july) == "14:45"
    # 15.01.2026 12:45 UTC → 13:45 (CET, UTC+1) — DST-Wechsel korrekt.
    january = calendar.timegm((2026, 1, 15, 12, 45, 0))
    assert ar.fmt_local_epoch(january) == "13:45"
    # Fehlertolerant im Render-Pfad.
    assert ar.fmt_local_epoch(None) == ""
    assert ar.fmt_local_epoch("unfug") == ""


def test_fmt_local_epoch_respects_app_timezone_env(monkeypatch):
    import calendar

    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    july = calendar.timegm((2026, 7, 24, 12, 45, 0))
    assert ar.fmt_local_epoch(july) == "12:45"
    # Unbekannte Zone fällt auf UTC zurück statt zu crashen.
    monkeypatch.setenv("APP_TIMEZONE", "Nirgend/Wo")
    assert ar.fmt_local_epoch(july) == "12:45"


def test_fmt_local_dt_interprets_naive_as_utc():
    """DB-Zeitstempel (SQLite ``CURRENT_TIMESTAMP`` = UTC-naiv) müssen in
    lokaler Zeit angezeigt werden."""
    assert ar.fmt_local_dt("2026-07-24 12:45:00") == "24.07.2026 14:45"
    assert ar.fmt_local_dt("2026-01-15 12:45:00") == "15.01.2026 13:45"
    # Aware Werte werden konvertiert, nicht doppelt verschoben.
    assert ar.fmt_local_dt("2026-07-24 12:45:00+00:00") == "24.07.2026 14:45"
    # Fail-open: None → Platzhalter, Unlesbares → Rohwert.
    assert ar.fmt_local_dt(None) == "–"
    assert ar.fmt_local_dt("kein datum") == "kein datum"


def test_progress_checklist_shows_local_start_time():
    import calendar

    job = {
        "agents_ticker": "SAP.DE",
        "agents": ["Market Analyst"],
        "agent_states": {},
        "started_at": float(calendar.timegm((2026, 7, 24, 12, 45, 0))),
    }
    assert "gestartet 14:45" in str(ar.progress_checklist(job))


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


def test_render_helpers_tolerate_service_dict_agents():
    """Regression: der ``run``-SSE-Event des Service liefert ``agents`` als
    Liste von Dicts ({team, name, status}). Vor dem Fix warf jeder Render-Pfad
    eines laufenden Jobs ``TypeError: unhashable type: 'dict'`` — Fortschritts-
    und Statuskarte erschienen nie, die Tiefenanalyse schien „nicht zu starten".
    """
    job = {
        "status": "running",
        "stage": "Market Analyst (in_progress)",
        "agents_ticker": "AAPL",
        "started_at": 1_753_300_000.0,
        "agents": [
            {"team": "Analyst Team", "name": "Market Analyst", "status": "completed"},
            {"team": "Analyst Team", "name": "Sentiment Analyst", "status": "pending"},
            {"team": "Research Team", "name": "Bull Researcher", "status": "pending"},
            {"team": "Portfolio Management", "name": "Portfolio Manager", "status": "pending"},
        ],
        "agent_states": {
            "Market Analyst": "completed",
            "Sentiment Analyst": "in_progress",
        },
    }

    stats = ar.progress_stats(job)
    assert stats["agents"] == [
        "Market Analyst", "Sentiment Analyst", "Bull Researcher", "Portfolio Manager"
    ]
    assert stats["total"] == 4
    assert stats["done"] == 1
    assert stats["active"] == "Sentiment Analyst"

    # Beide Render-Pfade laufender Jobs dürfen nicht mehr crashen.
    assert "ms-agent-pipe" in str(ar.progress_checklist(job))
    assert "Laufend · AAPL" in str(ar.compact_status_card("AAPL", job))
