"""Tests des Markdown-Reports (Abschnitte, Escaping, Leerzustände)."""

from __future__ import annotations

from datetime import datetime

from app.core.analyst import report
from app.core.analyst.runner import CompanyResult, RunBudget, RunResult
from app.core.analyst.watchlist import WatchItem
from app.core.diagnostics import SEV_WARNING, Diagnostic


def _result(**kw) -> RunResult:
    base = dict(
        run_id="20261021_090000", trigger="cli", scope="portfolio", started_at=datetime(2026, 10, 21, 9),
        finished_at=datetime(2026, 10, 21, 9, 5), status="done", dry_run=False, model="m",
        budget=RunBudget(max_companies=25, max_llm_calls=80, llm_calls=4, av_calls=9),
    )
    base.update(kw)
    return RunResult(**base)


def test_table_escapes_pipes_and_empty():
    assert report._table(["a"], []) == "_keine Einträge_"
    t = report._table(["a", "b"], [["x|y", None]])
    assert "x\\|y" in t and "| – |" in t


def test_empty_run_renders_all_sections(tmp_path):
    res = _result(diagnostics=[Diagnostic(SEV_WARNING, "calendar_unavailable", "x")])
    path = report.write_report(res, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name == "ki_analyst_20261021_090000.md"
    for sec in ("# KI-Analyst — Lauf", "## Trends & Profiteure", "## Themen (Universum)", "## Vorschläge",
                "## Titel", "## Firmenübergreifende Verknüpfungen", "## Ereignisse", "## Diagnose"):
        assert sec in text
    assert "_Keine Trends in diesem Lauf._" in text and "calendar_unavailable" in text
    assert "4 LLM-Calls (max. 80)" in text


def test_dry_run_inputs_and_company_sections(tmp_path):
    item = WatchItem("SAP", "SAP", "SAP SE", av_symbol="SAP.DEX", zone="KANDIDAT", composite_score=88.0, reasons={"kandidat"})
    comp = CompanyResult(item=item, market_reaction={"ret_since_report": -0.03, "days_since_report": 10, "ret_1m": -0.02})
    comp.insights = {
        "transcript_summary": {"status": "ok", "period": "2026Q2", "content": {"zusammenfassung": "Z", "guidance": {"aenderung": "angehoben", "details": "d"}, "ton": 4, "vertrauen_management": 3, "quant_abgleich": {"urteil": "neutral", "begruendung": "b"}, "kernaussagen": ["K1"], "risiken": []}},
        "synthesis": {"status": "ok", "content": {"faktor_zusammenspiel": [{"muster": "M", "faktoren": ["value"], "richtung": "neutral", "evidenz_urteil": "bestaetigt", "interpretation": "i", "regel_code": "value_trap"}],
                                                   "quellen_zusammenhaenge": [{"quellen": ["news", "insider"], "befund": "B", "richtung": "bestaetigend", "bedeutung": 4, "belege": ["x"]}],
                                                   "nicht_eingepreiste_themen": [{"thema": "T", "art": "produktzyklus", "richtung": "positiv", "zeithorizont": "mittel", "konfidenz": 0.6, "warum_nicht_eingepreist": "w", "bestaetigung_durch": "c"}]}},
    }
    comp.links = [{"related_name": "Mercedes", "related_uid": "MBG", "relation": "kunde", "context": "c", "source": "Call"}]
    res = _result(dry_run=True, companies=[comp], trend_inputs={"n_news": 3, "news_per_topic": {"technology": 3}, "n_candidates": 2, "candidates_per_group": {"Software": 2}, "mode": "single"},
                  trends=[{"title": "Tr", "description": "d", "kind": "makro", "horizon": "kurz", "strength": 3, "confidence": 0.5, "evidence": [{"quelle": "news", "referenz": "[1]"}], "industry_groups": ["Software"], "loser_note": "L"}],
                  trend_links=[{"trend_idx": 0, "ticker": "SAP", "name": "SAP SE", "role": "direkt", "pricing_llm": "früh", "pricing_quant": "früh", "action": "Watchlist", "reasoning": ["a", "b"], "zone": "KANDIDAT", "composite_score": 88.0}],
                  themes=[{"title": "Th", "kind": "sektor", "direction": "positiv", "confidence": 0.7, "uids": ["SAP"], "confirmation": "x", "description": "desc"}],
                  cross_links=[{"uid": "SAP", "related_name": "MBG", "relation": "kunde", "context": "c"}])
    path = report.write_report(res, tmp_path)
    text = path.read_text(encoding="utf-8")
    assert path.name.endswith("_dry.md") and "(Trockenlauf)" in text
    assert "Trend-Scan-Eingaben" in text and "| technology | 3 |" in text
    assert "### 1. Tr" in text and "a → b" in text and "Verlierer-Hinweis: L" in text
    assert "**Th:** desc" in text
    assert "Kurs seit Bericht -3,0 %" in text
    assert "**Earnings-Call 2026Q2:** Z" in text and "Guidance: angehoben" in text
    assert "**Faktor-Zusammenspiel**" in text and "| M | value |" in text
    assert "news ↔ insider" in text and "| T | produktzyklus |" in text
    assert "| Mercedes | MBG | kunde |" in text
    assert "| SAP | MBG | kunde | c |" in text
