"""Smoke-Tests der Seite „KI-Analyst" (Layout, Tabellen, Key-Hinweise)."""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from app.main import create_app

create_app()

from app.core.analyst import scheduler, store  # noqa: E402
from app.core.state import STATE  # noqa: E402
from app.pages import ki_analyst as page  # noqa: E402
from app.ui import analyst as ui  # noqa: E402


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


def test_layout_renders_and_registers():
    import dash

    ids = {c.id for c in _walk(page.layout()) if getattr(c, "id", None) is not None}
    for cid in ("ka-status", "ka-trends", "ka-themes", "ka-suggestions", "ka-analyses", "ka-events", "ka-runs",
                "ka-poll", "ka-fp", "ka-trend-early", "ka-kind-filter"):
        assert cid in ids, cid
    assert any(p["path"] == "/ki-analyst" and p["name"] == "KI-Analyst" for p in dash.page_registry.values())


def test_status_card_shows_key_hints(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    monkeypatch.setenv("ALPHAVANTAGE_API_KEY", "x")
    view = str(page._status_card({"status": "idle", "running": False, "last_run_id": "r1", "last_status": "done",
                                  "last_finished_at": datetime(2026, 10, 21, 9), "next_run_at": None, "last_error": "boom", "progress": None}))
    assert "OLLAMA_API_KEY ist nicht gesetzt" in view and "ALPHAVANTAGE_API_KEY" not in view
    assert "r1" in view and "boom" in view and "Jetzt ausführen" in view


def test_suggestions_table_and_badges():
    rows = [
        {"id": 1, "uid": "SAP", "ticker": "SAP", "suggestion_type": "EINSTIEG_PRÜFEN", "confidence": 0.8, "quant_zone": "KANDIDAT",
         "quant_score": 88.0, "quant_consistency": "bestaetigt", "n_interplay": 2, "n_unpriced": 1, "rationale": "Erster Satz. Zweiter Satz. Dritter.",
         "created_at": datetime(2026, 10, 21, 9), "status": "open"},
        {"id": 2, "uid": "MBG", "ticker": "MBG", "suggestion_type": "REDUZIEREN_VERKAUF_PRÜFEN", "confidence": 0.5, "status": "done", "rationale": ""},
    ]
    view = str(page._suggestions_table(rows))
    assert "Einstieg prüfen" in view and "Reduzieren/Verkauf prüfen" in view
    assert "/einzelanalyse?ticker=SAP" in view and "ka-sugg-done" in view
    assert "Noch keine Vorschläge" in str(page._suggestions_table([]))
    badge = ui.suggestion_badge(rows[0])
    assert badge.href == "/ki-analyst" and "is-up" in str(badge)
    assert ui.suggestion_badge(None) is None
    assert ui.SUGGESTION_REC_CLASS["EINSTIEG_PRÜFEN"] == "buy"
    assert "bereits eingepreist" in str(ui.pricing_chip("bereits_eingepreist"))
    tb = ui.trend_badge({"trend_title": "Netzausbau", "role": "zulieferer", "pricing_quant": "früh", "reasoning": ["a", "b"]})
    assert "Zulieferer" in str(tb) and "is-up" in str(tb)


def test_trends_and_themes_views(monkeypatch):
    monkeypatch.setattr(STATE, "scored", pd.DataFrame({"uid": ["SAP"], "ticker": ["SAP"]}), raising=False)
    trends = [{"title": "Netzausbau", "description": "d", "kind": "energie_transport", "horizon": "mittel", "strength": 4, "confidence": 0.8,
               "evidence": [{"quelle": "news", "referenz": "[1]"}], "industry_groups": ["Utilities"], "loser_note": "L",
               "links": [{"uid": "SAP", "ticker": "SAP", "role": "enabler", "pricing_quant": "teilweise_eingepreist", "pricing_llm": "früh", "action": "Watchlist", "reasoning": ["x", "y"]}]}]
    view = str(page._trends_view(trends, early_only=False))
    assert "Netzausbau (1 Profiteure)" in view and "Enabler" in view and "x → y" in view and "Verlierer-Hinweis: L" in view
    view_early = str(page._trends_view(trends, early_only=True))
    assert "keine Profiteure (Filter: nur früh)" in view_early
    assert "Keine Trends" in str(page._trends_view([], False))

    themes = [{"title": "KI-Capex", "kind": "sektor", "direction": "positiv", "confidence": 0.7, "uids": ["SAP", "GHOST"], "description": "desc", "confirmation": "Q4"}]
    links = [{"uid": "SAP", "ticker": "SAP", "related_uid": "GHOST", "related_name": "Ghost", "relation": "kunde", "context": "c", "source": "themes"}]
    view = str(page._themes_view(themes, links))
    assert "KI-Capex" in view and "/einzelanalyse?ticker=SAP" in view and "Firmenübergreifende Verknüpfungen" in view
    assert "Keine Themen" in str(page._themes_view([], []))


def test_synthesis_blocks_and_tables():
    content = {"faktor_zusammenspiel": [{"muster": "M", "faktoren": ["value"], "richtung": "neutral", "evidenz_urteil": "bestaetigt", "interpretation": "i"}],
               "quellen_zusammenhaenge": [{"quellen": ["news", "insider"], "befund": "B", "richtung": "bestaetigend", "bedeutung": 4}],
               "nicht_eingepreiste_themen": [{"thema": "T", "art": "produktzyklus", "richtung": "positiv", "zeithorizont": "kurz", "konfidenz": 0.6, "warum_nicht_eingepreist": "w", "bestaetigung_durch": "c"}]}
    view = str(html_div(page.synthesis_blocks(content, [{"related_uid": "MBG", "related_ticker": "MBG", "relation": "kunde", "context": "c", "source": "Call", "direction": "out"}])))
    assert "news ↔ insider" in view and "Verbundene Unternehmen" in view and "/einzelanalyse?ticker=MBG" in view
    assert "nicht verfügbar" in str(html_div(page.synthesis_blocks(None)))
    events = [{"uid": "SAP", "ticker": "SAP", "kind": "news", "event_date": datetime(2026, 10, 20).date(), "title": "3 neue News", "run_id": "r1"}]
    assert "News" in str(page._events_table(events)) and "Keine Ereignisse" in str(page._events_table([]))
    runs = [{"run_id": "r1", "status": "done", "trigger": "cli", "started_at": datetime(2026, 10, 21, 9), "n_companies": 1, "n_events": 2, "n_llm_calls": 3, "n_av_calls": 4, "n_themes": 1, "n_trends": 1, "report_path": "reports/x.md",
             "diagnostics_json": '[{"severity": "Warnung", "code": "x", "message": "m", "uid": null}]'}]
    view = str(page._runs_table(runs))
    assert "reports/x.md" in view and "Diagnose (KI-Analyst" in view
    assert "Noch keine Läufe" in str(page._runs_table([]))


def html_div(children):
    from dash import html

    return html.Div(children)


def test_tables_callback_with_empty_store(monkeypatch):
    for name in ("list_trends", "list_themes", "list_company_links", "list_suggestions", "list_runs", "list_insights", "list_events"):
        monkeypatch.setattr(store, name, lambda *a, **k: [])
    out = page._tables(None, [], "")
    assert len(out) == 11 and out[1] == "0 Trends" and out[10] == "0 Läufe"


def test_poll_and_run_now(monkeypatch):
    monkeypatch.setattr(store, "list_runs", lambda limit=1: [{"run_id": "r9"}])
    monkeypatch.setattr(scheduler, "get_status", lambda: {"status": "idle", "running": False, "last_run_id": "r9", "last_status": "done", "progress": None, "next_run_at": None, "last_error": None, "last_finished_at": None})
    card, fp = page._poll(1, None)
    assert "r9" in str(card) and fp == "idle|r9|None|r9"
    _, fp2 = page._poll(2, fp)
    from dash import no_update

    assert fp2 is no_update
    monkeypatch.setattr(scheduler, "trigger_now", lambda: (False, "Kein Universum geladen."))
    assert "Kein Universum" in str(page._run_now(1))


def test_dashboard_analyst_cell():
    from app.pages import dashboard as dp

    assert "–" in str(dp._analyst_cell(None, None))
    cell = dp._analyst_cell({"suggestion_type": "EINSTIEG_PRÜFEN", "rationale": "r"}, {"trend_title": "Netz", "role": "enabler", "pricing_quant": "früh", "reasoning": ["a"]})
    view = str(cell)
    assert "Einstieg prüfen" in view and "is-buy" in view and "▲ Trend" in view and "/ki-analyst" in view


def test_einzelanalyse_badges_and_card(monkeypatch):
    from app.pages import einzelanalyse as ea

    monkeypatch.setattr(store, "list_suggestions", lambda limit=1, uid=None, status=None: [
        {"id": 1, "uid": uid, "ticker": uid, "suggestion_type": "ÜBERPRÜFEN", "confidence": 0.6, "rationale": "Grund.", "status": "open", "created_at": datetime(2026, 10, 21, 9)}])
    monkeypatch.setattr(store, "load_trend_links_for", lambda uid, limit=3: [{"trend_title": "Netz", "role": "zulieferer", "pricing_quant": "früh", "pricing_llm": "frueh", "action": "Kandidat prüfen", "reasoning": ["a", "b"]}])
    monkeypatch.setattr(store, "load_latest_synthesis", lambda uid: {"content": {"faktor_zusammenspiel": [], "quellen_zusammenhaenge": [{"quellen": ["news", "quant"], "befund": "B", "richtung": "neutral", "bedeutung": 2}], "nicht_eingepreiste_themen": []}})
    monkeypatch.setattr(store, "load_company_links_for", lambda uid, limit=50: [])
    monkeypatch.setattr(store, "load_insight", lambda uid, kind, period=None: {"period": "2026Q2", "content": {"zusammenfassung": "Z", "guidance": {"aenderung": "angehoben"}, "ton": 4, "vertrauen_management": 3}} if kind == "transcript_summary" else None)
    badges = ea._analyst_hero_badges("SAP")
    view = str(badges)
    assert "Überprüfen" in view and "TREND" in view
    card = str(ea._analyst_card("SAP"))
    assert "KI-Analyst" in card and "Grund." in card and "Earnings-Call 2026Q2" in card and "news ↔ quant" in card and "Zulieferer" in card

    monkeypatch.setattr(store, "list_suggestions", lambda **k: [])
    monkeypatch.setattr(store, "load_trend_links_for", lambda uid, limit=3: [])
    monkeypatch.setattr(store, "load_latest_synthesis", lambda uid: None)
    assert ea._analyst_card("SAP") is None and ea._analyst_hero_badges("SAP") == []


def test_settings_card_and_save(monkeypatch):
    from app.pages import einstellungen as es

    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    view = str(es._analyst_card())
    assert "analyst-model" in view and "nicht gesetzt" in view
    saved = {}
    monkeypatch.setattr(es, "save_settings", lambda s: saved.setdefault("ok", True))
    out = es._save_analyst(1, "deepseek-v3.1:671b", "custom", "SAP, MBG~mercedes\nNOPE", True, "90", 10, 40, 14, 30, 0.5,
                           "70000", 120, True, False, True, True, False, True, "technology, energy_transportation", 25, 4, 6, 120, 3, "out")
    s = STATE.settings
    assert saved.get("ok") and "gespeichert" in str(out)
    assert s.analyst_model == "deepseek-v3.1:671b" and s.analyst_scope == "custom"
    assert s.analyst_custom_tickers == ["SAP", "MBG~mercedes", "NOPE"]
    assert s.analyst_enabled is True and s.analyst_interval_minutes == 90
    assert s.analyst_include_news is False and s.analyst_themes_enabled is False
    assert s.analyst_trend_topics == ["technology", "energy_transportation"]
    assert s.analyst_trend_max_candidates == 120 and s.analyst_report_dir == "out"
    assert s.analyst_news_min_relevance == 0.5 and s.analyst_transcript_max_chars == 70000
    # Clamping / Defaults bei Unsinn
    es._save_analyst(2, "", "weird", "", False, "x", -5, None, 0, 0, "y", 1, 1, True, True, True, True, True, True, "", 0, 99, 0, 1, 0, "")
    assert s.analyst_model == "gpt-oss:120b" and s.analyst_scope == "portfolio" and s.analyst_interval_minutes == 360
    assert s.analyst_max_companies_per_run == 1 and s.analyst_max_llm_calls_per_run == 80
    assert s.analyst_trend_max_trends == 20 and s.analyst_trend_max_candidates == 10 and s.analyst_report_dir == "reports"
