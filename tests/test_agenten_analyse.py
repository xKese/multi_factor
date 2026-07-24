"""Tests für Symbol-Treffer-Liste und Verlauf der Ad-hoc-Agenten-Analyse."""

from __future__ import annotations

import pandas as pd

# register_page() im Seitenmodul verlangt eine instanziierte Dash-App.
from app.main import create_app

create_app()

from app.core import agents_client  # noqa: E402
from app.core.state import STATE  # noqa: E402
from app.pages import agenten_analyse as aa  # noqa: E402


_RESULTS = [
    {"symbol": "MBG.F", "name": "Mercedes-Benz Group AG", "region": "Frankfurt"},
    {"symbol": "MBG.MX", "name": "Mercedes-Benz Group AG", "region": "Mexico"},
]


def test_search_results_tickerlike_fallback(monkeypatch):
    monkeypatch.setattr(agents_client, "symbol_search", lambda q: ([], None))
    results, note = aa._search_results("googl")
    assert results[0]["symbol"] == "GOOGL"
    assert results[0]["name"] == "Direkt übernehmen"

    # Namensartige Begriffe erzeugen keine Pseudo-Option.
    results2, note2 = aa._search_results("mercedes")
    assert results2 == []
    assert "Keine Treffer" in note2

    # Kein Duplikat, wenn der Ticker bereits in den Treffern steckt.
    monkeypatch.setattr(agents_client, "symbol_search", lambda q: (_RESULTS, None))
    results3, _ = aa._search_results("MBG.F")
    assert [r["symbol"] for r in results3].count("MBG.F") == 1


def test_hits_list_marks_selection(monkeypatch):
    monkeypatch.setattr(STATE, "scored", pd.DataFrame(), raising=False)
    view = str(aa._hits_list(_RESULTS, selected="MBG.F"))
    assert "is-selected" in view
    assert view.count("ms-agent-hit'") or "ms-agent-hit" in view
    assert "Mercedes-Benz Group AG" in view


def test_universe_tag(monkeypatch):
    scored = pd.DataFrame(
        {"ticker": ["MBG"], "name": ["Mercedes-Benz Group AG"], "total_score": [64.2]}
    )
    monkeypatch.setattr(STATE, "scored", scored, raising=False)
    # Basis-Ticker des suffigierten Symbols steht im Universum.
    label, in_uni = aa._universe_tag("MBG.DE")
    assert in_uni and label == "Im Universum · Quant 64,2"
    label2, in_uni2 = aa._universe_tag("AAPL")
    assert not in_uni2 and label2 == "Ad-hoc"


def test_history_table_delta_and_link(monkeypatch):
    scored = pd.DataFrame(
        {"ticker": ["SAP"], "name": ["SAP SE"], "total_score": [78.4]}
    )
    monkeypatch.setattr(STATE, "scored", scored, raising=False)
    df = pd.DataFrame(
        [
            {
                "ticker": "SAP",
                "run_id": "r1",
                "in_universe": 1,
                "rating": "Overweight",
                "provider": "anthropic",
                "total_score": 78.4,
                "classification": "B+ · Sehr Gut",
                "factor_context_json": '{"recommendation": "BUY"}',
                "created_at": "2026-07-21 14:32:00",
            },
            {
                "ticker": "NVO",
                "run_id": "r2",
                "in_universe": 0,
                "rating": "Underweight",
                "provider": "openai",
                "total_score": None,
                "classification": None,
                "factor_context_json": None,
                "created_at": "2026-07-15 16:48:00",
            },
        ]
    )
    view = str(aa._history_table(df))
    assert "ms-toptable" in view
    assert "bestätigt" in view  # Overweight vs BUY
    assert "Einzelanalyse ›" in view  # Link nur für Universum-Titel
    assert "78,4" in view
    assert "Ad-hoc" in view
    assert "SAP SE" in view
