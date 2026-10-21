"""Tests der deterministischen Trend-Scan-Bausteine."""

from __future__ import annotations

from datetime import datetime

import pandas as pd
import pytest

from app.core.analyst import trend_scan as ts
from app.core.av_client import AlphaVantageError
from app.core.config import Settings


def _scored(n_cg: int = 6):
    rows = []
    for i in range(n_cg):
        rows.append({"uid": f"CG{i}", "ticker": f"CG{i}", "name": f"Capital {i}", "industry_group": "Capital Goods",
                     "industry": "Machinery", "sector": "Industrials", "region": "EU", "filter_pass": True,
                     "composite_score": 90 - i * 5, "zone_v2": "KANDIDAT" if i < 2 else "HALTEN",
                     "z_momentum": 0.1, "z_eps_revisions_3m": 0.0, "z_value": 0.5, "mom_12_1": 0.05})
    rows.append({"uid": "SW1", "ticker": "SW1", "name": "Soft 1", "industry_group": "Software & Services",
                 "industry": "Software", "sector": "IT", "region": "US", "filter_pass": True,
                 "composite_score": 70, "zone_v2": "HALTEN", "z_momentum": 1.2, "z_eps_revisions_3m": 0.8, "mom_12_1": 0.4})
    rows.append({"uid": "FAIL", "ticker": "FAIL", "name": "Filtered", "industry_group": None, "industry": "Banks",
                 "sector": "Financials", "region": "US", "filter_pass": False, "composite_score": 95, "zone_v2": "FILTER"})
    rows.append({"uid": "NOGRP", "ticker": "NOGRP", "name": "No group", "industry_group": None, "industry": None,
                 "sector": "Energy", "region": "US", "filter_pass": True, "composite_score": 50, "zone_v2": "VERKAUFEN"})
    return pd.DataFrame(rows)


class _AV:
    def __init__(self, feeds, fail_on=None, kind="error"):
        self.feeds, self.fail_on, self.kind = feeds, fail_on, kind
        self.calls = []

    def fetch_news_sentiment(self, topics=None, time_from=None, limit=50, requests_per_minute=70):
        self.calls.append((tuple(topics), time_from, limit))
        if self.fail_on and topics[0] == self.fail_on:
            raise AlphaVantageError("boom", kind=self.kind)
        return self.feeds.get(topics[0], [])


def _item(url, title, t, tickers=()):
    return {"title": title, "url": url, "time_published": t, "summary": "s" * 400, "source": "Src",
            "overall_sentiment_label": "Neutral", "overall_sentiment_score": 0.0,
            "topics": [{"topic": "Technology", "relevance_score": 1.0}],
            "ticker_sentiment": [{"ticker": tk, "relevance_score": rel} for tk, rel in tickers]}


def test_collect_market_news_one_call_per_topic_and_dedupe():
    s = Settings()
    s.analyst_trend_topics = ["technology", "energy_transportation", "manufacturing"]
    t1, t2 = datetime(2026, 10, 20), datetime(2026, 10, 19)
    av = _AV({
        "technology": [_item("u1", "A", t1, [("GRID", 0.7), ("X", 0.1)]), _item("u2", "B", t2)],
        "energy_transportation": [_item("u1", "A dup", t1)],
    })
    av_calls = []
    items, diags = ts.collect_market_news(s, datetime(2026, 10, 14), av, av_calls)
    assert len(av.calls) == 3 and av_calls == s.analyst_trend_topics
    assert [i["url"] for i in items] == ["u1", "u2"]
    assert items[0]["tickers"] == ["GRID"] and len(items[0]["summary"]) == 300
    assert diags == []

    text = ts.compact_news_for_prompt(items, max_items=10, with_summary=1)
    assert text.startswith("[1] 2026-10-20 · Src · A")
    assert "Ticker: GRID" in text
    assert text.count("\n    ") == 1
    assert ts.compact_news_for_prompt([]).startswith("(keine")


def test_collect_market_news_rate_limit_stops_but_keeps_items():
    s = Settings()
    s.analyst_trend_topics = ["technology", "energy_transportation", "manufacturing"]
    av = _AV({"technology": [_item("u1", "A", datetime(2026, 10, 20))]}, fail_on="energy_transportation", kind="rate_limit")
    items, diags = ts.collect_market_news(s, None, av)
    assert len(items) == 1 and len(av.calls) == 2
    assert [d.code for d in diags] == ["trend_news_failed"]
    av2 = _AV({"technology": []}, fail_on="technology", kind="error")
    items2, diags2 = ts.collect_market_news(s, None, av2)
    assert len(av2.calls) == 3 and len(diags2) == 1


def test_industry_group_profile_with_fallbacks():
    rows = ts.industry_group_profile(_scored())
    by = {r["industry_group"]: r for r in rows}
    assert by["Capital Goods"]["n"] == 6 and by["Capital Goods"]["n_kandidat"] == 2
    assert by["Capital Goods"]["avg_composite"] == pytest.approx(77.5)
    assert "Banks" in by and "Energy" in by  # Fallback industry → sector
    assert by["Banks"]["n_filter_pass"] == 0
    text = ts.render_group_profile(rows)
    assert text.splitlines()[0].startswith("Industriegruppe |")
    assert ts.industry_group_profile(pd.DataFrame()) == []


def test_candidate_table_topn_forced_and_cap():
    s = Settings()
    s.analyst_trend_top_per_group = 2
    s.analyst_trend_max_candidates = 100
    rows = ts.candidate_table(_scored(), s)
    uids = [r["uid"] for r in rows]
    assert "FAIL" not in uids
    assert uids[:2] == ["CG0", "CG1"] and "CG5" not in uids
    assert "SW1" in uids and "NOGRP" in uids

    rows2 = ts.candidate_table(_scored(), s, forced_uids={"CG5", "FAIL"})
    uids2 = [r["uid"] for r in rows2]
    assert "CG5" in uids2 and "FAIL" in uids2

    s.analyst_trend_max_candidates = 3
    rows3 = ts.candidate_table(_scored(), s, forced_uids={"NOGRP"})
    assert len(rows3) == 3 and "NOGRP" in [r["uid"] for r in rows3]
    # Die größte Gruppe (Capital Goods) gibt zuerst ab.
    assert sum(r["industry_group"] == "Capital Goods" for r in rows3) <= 2

    rows4 = ts.candidate_table(_scored(), s, group_whitelist=["software & services"])
    assert [r["uid"] for r in rows4] == ["SW1"]
    assert ts.render_candidates(rows4).count("\n") == 1
    assert ts.candidate_table(pd.DataFrame(), s) == []


def test_plan_trend_calls():
    s = Settings()
    s.analyst_trend_max_candidates = 10
    assert ts.plan_trend_calls(10, s) == "single"
    assert ts.plan_trend_calls(11, s) == "two_pass"


@pytest.mark.parametrize(
    "row, expected",
    [
        ({"z_momentum": 1.0, "z_eps_revisions_3m": 0.6}, ts.PRICING_PRICED),
        ({"mom_12_1": 0.35}, ts.PRICING_PRICED),
        ({"z_momentum": 0.0, "z_eps_revisions_3m": 0.1, "z_value": 0.3}, ts.PRICING_EARLY),
        ({"z_momentum": 0.5, "z_eps_revisions_3m": 0.1}, ts.PRICING_PARTIAL),
        ({"z_momentum": float("nan"), "z_eps_revisions_3m": None}, ts.PRICING_PARTIAL),
        ({"z_momentum": float("nan"), "z_value": 1.0}, ts.PRICING_EARLY),
    ],
)
def test_tag_pricing(row, expected):
    assert ts.tag_pricing(pd.Series(row)) == expected


def test_suggested_action_matrix():
    assert ts.suggested_action({"zone_v2": "KANDIDAT"}, ts.PRICING_EARLY, held=True) == ts.ACTION_POSITION
    assert ts.suggested_action({"zone_v2": "FILTER"}, ts.PRICING_EARLY, held=False) == ts.ACTION_INFO
    assert ts.suggested_action({"zone_v2": "KANDIDAT"}, ts.PRICING_EARLY, held=False) == ts.ACTION_CANDIDATE
    assert ts.suggested_action({"zone_v2": "HALTEN"}, ts.PRICING_PARTIAL, held=False) == ts.ACTION_WATCH
    assert ts.suggested_action({"zone_v2": "VERKAUFEN"}, ts.PRICING_EARLY, held=False) == ts.ACTION_WATCH


def test_validate_beneficiaries():
    s = Settings()
    s.analyst_trend_top_per_group = 10
    cands = {c["uid"]: c for c in ts.candidate_table(_scored(), s)}
    trend = {
        "titel": "Netzausbau",
        "profiteure": [
            {"uid": "CG0", "rolle": "direkt", "eingepreist": "frueh", "begruendungskette": ["a", "b"], "evidenz": "[1]"},
            {"uid": "", "ticker": "sw1", "rolle": "weird", "eingepreist": "bereits"},
            {"uid": "CG0", "rolle": "direkt"},
            {"uid": "GHOST", "ticker": "GHOST"},
            {"uid": "CG1", "rolle": "enabler", "eingepreist": "bereits"},
            "nicht-dict",
        ],
    }
    links, diags = ts.validate_beneficiaries(trend, cands, _scored(), {"CG1"}, max_n=5)
    assert [l["uid"] for l in links] == ["CG0", "SW1", "CG1"]
    assert links[0]["pricing_quant"] == ts.PRICING_EARLY and links[0]["pricing_llm"] == ts.PRICING_EARLY
    assert links[0]["action"] == ts.ACTION_CANDIDATE and links[0]["evidence"] == ["[1]"]
    assert links[1]["role"] == "direkt" and links[1]["pricing_quant"] == ts.PRICING_PRICED
    assert links[2]["action"] == ts.ACTION_POSITION
    codes = [d.code for d in diags]
    assert codes.count("trend_unknown_uid") == 1
    assert "trend_pricing_disagreement" in codes  # CG1: LLM „bereits“ vs. Quant „früh“
    assert ts.validate_beneficiaries({"profiteure": "x"}, cands, _scored(), set(), 5) == ([], [])
    links_capped, _ = ts.validate_beneficiaries(trend, cands, _scored(), set(), max_n=1)
    assert len(links_capped) == 1


@pytest.mark.parametrize(
    "value, expected",
    [
        ("frueh", ts.PRICING_EARLY), ("Früh", ts.PRICING_EARLY), ("früh erkennbar", ts.PRICING_EARLY),
        ("noch nicht eingepreist", ts.PRICING_EARLY), ("teilweise", ts.PRICING_PARTIAL),
        ("Teilweise eingepreist", ts.PRICING_PARTIAL), ("bereits", ts.PRICING_PRICED),
        ("bereits eingepreist", ts.PRICING_PRICED), ("vollständig eingepreist", ts.PRICING_PRICED),
        ("", None), (None, None), ("unbekannt", None),
    ],
)
def test_normalize_llm_pricing_variants(value, expected):
    assert ts.normalize_llm_pricing(value) == expected
