"""Tests der LLM-Tasks mit einem Fake-Ollama-Client."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from app.core.analyst import prompts, tasks
from app.core.analyst.events import AnalystEvent
from app.core.analyst.factor_interplay import InterplayHint
from app.core.analyst.watchlist import WatchItem
from app.core.config import Settings

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeOllama:
    """Liefert je Aufruf das nächste Ergebnis; protokolliert Prompts."""

    def __init__(self, results):
        self.results = list(results)
        self.calls: list[dict] = []

    def chat_json(self, system, user, schema, required, model="", temperature=0.0, timeout=300):
        self.calls.append({"system": system, "user": user, "schema": schema, "required": required, "model": model})
        item = self.results.pop(0) if self.results else None
        if item is None:
            return None, "nicht parsebar", 2
        return item, json.dumps(item), 1


@pytest.fixture()
def fake(monkeypatch):
    def _install(results):
        f = FakeOllama(results)
        monkeypatch.setattr(tasks, "ollama", f)
        return f

    return _install


def _item(held=False):
    return WatchItem("SAP", "SAP", "SAP SE", av_symbol="SAP.DEX", zone="HALTEN", composite_score=72.0, held=held)


def _scored():
    return pd.DataFrame(
        {
            "uid": ["SAP", "MBG", "SAN~sanofi", "SAN~bancosantander"],
            "ticker": ["SAP", "MBG", "SAN", "SAN"],
            "name": ["SAP SE", "Mercedes-Benz Group AG", "Sanofi", "Banco Santander SA"],
        }
    )


def _hints():
    return [InterplayHint("value_trap", "Value-Falle?", ("value", "momentum"), "h", "Frage?", {"z_value": 1.2}),
            InterplayHint("accruals_vs_cashflow_commentary", "Accruals", ("quality",), "h2", "Frage2?", {})]


def _transcript(n_segments=6, size=50):
    segs = [{"speaker": "Operator", "title": "Operator", "content": "Welcome " + "x" * size},
            {"speaker": "Jane", "title": "CEO", "content": "Guidance raised " + "y" * size}]
    for i in range(n_segments - 2):
        segs.append({"speaker": "Analyst " + str(i), "title": "Analyst, Bank", "content": "Question " + "q" * size})
    return {"quarter": "2026Q2", "segments": segs}


def test_split_and_chunk():
    tr = _transcript()
    prepared, qa = tasks.split_transcript(tr["segments"])
    assert len(prepared) == 2 and len(qa) == 4
    assert tasks.split_transcript([{"speaker": "a", "content": "x"}]) == ([{"speaker": "a", "content": "x"}], [])
    chunks = tasks.chunk_segments(tr["segments"], max_chars=150)
    assert len(chunks) >= 3 and sum(len(c) for c in chunks) == 6
    assert "Jane (CEO): Guidance" in tasks.render_segments(prepared)


def test_summarize_transcript_single_call(fake):
    f = fake([{"zusammenfassung": "ok", "guidance": {"aenderung": "angehoben", "details": ""}, "ton": 4,
               "quant_abgleich": {"urteil": "bestaetigt", "begruendung": "b"}}])
    s = Settings()
    res = tasks.summarize_transcript(_item(), _transcript(), {"scoring_version": "v2"}, _hints(), s)
    assert res.status == "ok" and res.n_calls == 1 and res.period == "2026Q2"
    assert res.kind == "transcript_summary" and res.model == "gpt-oss:120b"
    user = f.calls[0]["user"]
    assert "### Q&A" in user and "[value_trap] Frage?" in user and "Prepared Remarks" in user
    assert f.calls[0]["schema"] is prompts.TRANSCRIPT_SCHEMA
    assert f.calls[0]["system"] == prompts.SYSTEM_PROMPT


def test_summarize_transcript_map_reduce(fake):
    chunk = {"kernaussagen": ["a"]}
    final = {"zusammenfassung": "ok", "guidance": {"aenderung": "bestaetigt", "details": ""}, "ton": 3,
             "quant_abgleich": {"urteil": "neutral", "begruendung": ""}}
    f = fake([chunk, None, chunk, final])
    s = Settings()
    s.analyst_transcript_max_chars = 150
    res = tasks.summarize_transcript(_item(), _transcript(), {}, [], s, model="m2")
    assert res.status == "ok"
    assert res.n_calls == 1 + 2 + 1 + 1  # 3 Chunks (einer unparsed → 2 Calls) + Reduce
    assert res.model == "m2"
    assert f.calls[-1]["schema"] is prompts.TRANSCRIPT_SCHEMA
    assert "Teilauswertungen" in f.calls[-1]["user"]

    f2 = fake([None, None, None])
    res2 = tasks.summarize_transcript(_item(), _transcript(), {}, [], s)
    assert res2.status == "unparsed" and res2.content is None and len(f2.calls) == 3


def test_digest_news_unparsed(fake):
    fake([None])
    res = tasks.digest_news(_item(), [{"title": "A", "time": "2026-10-20", "summary": "s", "sentiment": 0.3}], {}, Settings(), period="p")
    assert res.status == "unparsed" and res.raw_text == "nicht parsebar" and res.n_calls == 2


def test_resolve_company_mentions():
    mentions = _load("ollama_synthesis_sample.json")["verbundene_unternehmen"]
    links = tasks.resolve_company_mentions(mentions, _scored(), "SAP")
    assert [l["related_name"] for l in links] == ["Mercedes-Benz Group", "Unknown Supplier Ltd", "Banco Santander"]
    assert links[0]["related_uid"] == "MBG"  # Prefix-Match
    assert links[1]["related_uid"] is None and links[1]["related_ticker"] == "ZZZ"
    assert links[2]["related_uid"] == "SAN~bancosantander" and links[2]["relation"] == "sonstiges"
    # Mehrdeutiger Ticker "SAN" → keine uid
    amb = tasks.resolve_company_mentions([{"name": "", "ticker": "SAN"}], _scored(), "SAP")
    assert amb[0]["related_uid"] is None
    assert tasks.resolve_company_mentions(mentions, None, "SAP")[0]["related_uid"] is None


def test_synthesize_company_validates(fake):
    f = fake([_load("ollama_synthesis_sample.json")])
    item = _item(held=False)
    events = [AnalystEvent("earnings", "SAP", "SAP", "2026-07-28", date(2026, 7, 28), "Quartalszahlen", {"surprise_pct": 4.5}),
              AnalystEvent("insider", "SAP", "SAP", "2026-10-15", date(2026, 10, 15), "Insider", {"n_buys": 1, "n_sells": 0, "net_shares": 5000, "transactions": []}),
              AnalystEvent("news", "SAP", "SAP", "x", date(2026, 10, 20), "News", {"items": [{"title": "N1", "time": "2026-10-20", "summary": "s"}]})]
    insights = {"transcript_summary": {"content": {"zusammenfassung": "ok"}}}
    prev = {"created_at": datetime(2026, 9, 1), "content": {"vorschlag": {"typ": "INFO", "konfidenz": 0.4, "begruendung": "alt"},
                                                            "nicht_eingepreiste_themen": [{"thema": "Altes Thema"}],
                                                            "quellen_zusammenhaenge": [{"befund": "alter Befund"}]}}
    res = tasks.synthesize_company(item, events, insights, {"scoring_version": "v2"}, _hints(), {"ret_1m": -0.02},
                                   prev, Settings(), held=False, scored=_scored(), period="2026-10-21")
    user = f.calls[0]["user"]
    for sec in ("1. Quant-Prior", "2. Markt-Reaktion", "4. Earnings-Überraschung", "6. Earnings-Call",
                "8. News-Items", "9. Insider", "11. Vorherige Analyse"):
        assert sec in user
    assert "## 7. News-Digest\nnicht verfügbar" in user
    assert "Altes Thema" in user and "alter Befund" in user
    assert "nicht im Bestand" in user

    c = res.insight.content
    assert res.insight.status == "ok"
    # Hinweis accruals nicht adressiert → ergänzt
    codes = [x["regel_code"] for x in c["faktor_zusammenspiel"]]
    assert codes == ["value_trap", None, "accruals_vs_cashflow_commentary"]
    assert [d.code for d in res.insight.diagnostics] == ["interplay_unaddressed"]
    assert len(c["quellen_zusammenhaenge"]) == 1
    assert c["quellen_zusammenhaenge"][0]["quellen"] == ["earnings", "insider"]
    assert len(c["nicht_eingepreiste_themen"]) == 1 and c["nicht_eingepreiste_themen"][0]["konfidenz"] == 1.0
    # HALTEN ohne Bestand → ÜBERPRÜFEN
    assert c["vorschlag"]["typ"] == "ÜBERPRÜFEN" and c["vorschlag"]["disclaimer"]
    assert res.suggestion["suggestion_type"] == "ÜBERPRÜFEN" and res.suggestion["n_interplay"] == 3
    assert res.suggestion["n_unpriced"] == 1 and res.suggestion["quant_zone"] == "HALTEN"
    # String-Belege werden als Quant-Referenz übernommen statt verworfen.
    assert len(res.suggestion["evidence"]) == 2
    assert [l["related_uid"] for l in res.links] == ["MBG", None, "SAN~bancosantander"]
    assert all(l["uid"] == "SAP" for l in res.links)

    # Bestandstitel: EINSTIEG → ÜBERPRÜFEN; HALTEN bleibt
    fake([{**_load("ollama_synthesis_sample.json"), "vorschlag": {"typ": "EINSTIEG_PRÜFEN", "konfidenz": "x", "begruendung": "b"}}])
    res2 = tasks.synthesize_company(_item(True), [], {}, {}, [], {}, None, Settings(), held=True, scored=_scored())
    assert res2.suggestion["suggestion_type"] == "ÜBERPRÜFEN" and res2.suggestion["confidence"] is None
    fake([{**_load("ollama_synthesis_sample.json")}])
    res3 = tasks.synthesize_company(_item(True), [], {}, {}, [], {}, None, Settings(), held=True, scored=_scored())
    assert res3.suggestion["suggestion_type"] == "HALTEN"

    fake([None])
    res4 = tasks.synthesize_company(_item(), [], {}, {}, [], {}, None, Settings(), held=False)
    assert res4.insight.status == "unparsed" and res4.suggestion is None and res4.links == []


def test_extract_and_cluster_themes(fake):
    content = _load("ollama_synthesis_sample.json")
    themes = tasks.extract_company_themes("SAP", "SAP", "IT", "EU", content)
    assert len(themes) == 1 and themes[0]["thema"] == "Neue Produktlinie"
    assert tasks.extract_company_themes("x", "x", None, None, None) == []

    # < 2 Beitragsfirmen → kein Call
    f = fake([_load("ollama_themes_sample.json")])
    res = tasks.cluster_themes("r1", themes, [], Settings(), {"SAP", "MBG"})
    assert res.insight is None and res.n_calls == 0 and f.calls == []

    two = themes + tasks.extract_company_themes("MBG", "MBG", "Auto", "EU", content)
    res = tasks.cluster_themes("r1", two, [{"uid": "SAP", "ticker": "SAP", "related_name": "Mercedes", "related_uid": "MBG", "relation": "kunde", "context": "c"}], Settings(), {"SAP", "MBG"})
    assert res.n_calls == 1 and len(res.themes) == 1
    assert res.themes[0]["uids"] == ["SAP", "MBG"] and res.themes[0]["title"] == "KI-Capex-Zyklus"
    assert len(res.links) == 1 and res.links[0]["related_uid"] == "MBG" and res.links[0]["source"] == "themes"
    assert "Unternehmensverknüpfungen" in f.calls[0]["user"]

    fake([None])
    res2 = tasks.cluster_themes("r1", two, [], Settings(), {"SAP", "MBG"})
    assert res2.insight.status == "unparsed" and res2.themes == []


def _trend_scored():
    rows = []
    for i in range(5):
        rows.append({"uid": f"CG{i}", "ticker": f"CG{i}", "name": f"Capital {i}", "industry_group": "Capital Goods",
                     "region": "EU", "filter_pass": True, "composite_score": 90 - i, "zone_v2": "KANDIDAT",
                     "z_momentum": 0.1, "z_eps_revisions_3m": 0.0})
    rows.append({"uid": "UT1", "ticker": "UT1", "name": "Util", "industry_group": "Utilities", "region": "US",
                 "filter_pass": True, "composite_score": 60, "zone_v2": "HALTEN", "z_momentum": 1.0, "z_eps_revisions_3m": 0.9})
    rows.append({"uid": "BK1", "ticker": "BK1", "name": "Bank", "industry_group": "Banks", "region": "US",
                 "filter_pass": True, "composite_score": 60, "zone_v2": "HALTEN"})
    return pd.DataFrame(rows)


def test_scan_trends_two_pass(fake):
    s = Settings()
    s.analyst_trend_max_candidates = 3
    s.analyst_trend_top_per_group = 10
    f = fake([_load("ollama_trends_a_sample.json"), _load("ollama_trends_b_sample.json")])
    news = [{"title": "Grid", "url": "u", "time_published": datetime(2026, 10, 20), "source": "S", "summary": "x", "topics": ["Energy"], "tickers": []}]
    res = tasks.scan_trends("r1", news, [{"title": "T", "kind": "sektor", "direction": "positiv", "description": "d"}],
                            _trend_scored(), s, held_uids=set())
    assert len(f.calls) == 2 and res.n_calls == 2
    assert f.calls[0]["schema"] is prompts.TREND_GROUPS_SCHEMA
    assert f.calls[1]["schema"] is prompts.TREND_SCHEMA
    # Pass B: Kandidaten nur aus Utilities/Capital Goods (+ SECOND_ORDER), Banks nicht
    assert "BK1" not in f.calls[1]["user"] and "UT1" in f.calls[1]["user"]
    assert len(res.trends) == 1 and res.trends[0]["strength"] == 5
    assert res.trends[0]["loser_note"] == "Stromintensive Industrie"
    assert [l["uid"] for l in res.links] == ["CG0"] and res.links[0]["trend_idx"] == 0
    assert res.links[0]["pricing_quant"] == "früh" and res.links[0]["action"] == "Kandidat prüfen"
    assert "trend_unknown_uid" in [d.code for d in res.diagnostics]
    assert res.insight.content["mode"] == "two_pass"


def test_scan_trends_single_and_skips(fake):
    s = Settings()
    f = fake([_load("ollama_trends_b_sample.json")])
    res = tasks.scan_trends("r1", [], [{"title": "T", "kind": "makro", "direction": "gemischt", "description": "d"}],
                            _trend_scored(), s, held_uids={"CG0"})
    assert len(f.calls) == 1 and f.calls[0]["schema"] is prompts.TREND_SCHEMA
    assert "Kandidatentabelle" in f.calls[0]["user"] and "(keine Markt-News" in f.calls[0]["user"]
    assert res.links[0]["action"] == "Position prüfen"

    res2 = tasks.scan_trends("r1", [], [], _trend_scored(), s, set())
    assert res2.insight is None and [d.code for d in res2.diagnostics] == ["trend_scan_no_input"]

    fake([None])
    res3 = tasks.scan_trends("r1", [], [{"title": "T"}], _trend_scored(), s, set())
    assert res3.insight.status == "unparsed" and res3.trends == []

    fake([{"trends": []}])
    res4 = tasks.scan_trends("r1", [], [{"title": "T"}], _trend_scored(), s, set())
    assert [d.code for d in res4.diagnostics] == ["trend_scan_empty"]


@pytest.mark.parametrize(
    "vorschlag, expected_typ, expected_conf, expected_kons",
    [
        ({"typ": "Einstieg prüfen", "konfidenz": "0,7", "begruendung": "b", "quant_konsistenz": "bestätigt"}, "EINSTIEG_PRÜFEN", 0.7, "bestaetigt"),
        ({"typ": "Reduzieren / Verkauf prüfen", "konfidenz": "70 %", "begruendung": "b", "quant_konsistenz": "widerspricht"}, "ÜBERPRÜFEN", 0.7, "widerspricht"),
        ({"typ": "halten", "konfidenz": 65, "begruendung": "b", "quant_konsistenz": {"urteil": "bestätigt"}}, "ÜBERPRÜFEN", 0.65, "bestaetigt"),
        ({"empfehlung": "Überprüfen", "confidence": 0.8, "rationale": "b", "konsistenz": "unklar"}, "ÜBERPRÜFEN", 0.8, "neutral"),
        ({"type": "info", "konfidenz": None, "begründung": "b"}, "INFO", None, "neutral"),
        ("Kaufen", "EINSTIEG_PRÜFEN", None, "neutral"),
    ],
)
def test_norm_vorschlag_variants(vorschlag, expected_typ, expected_conf, expected_kons):
    content = {"vorschlag": vorschlag}
    out, diags = tasks._norm_vorschlag(content, held=False)
    assert out["typ"] == expected_typ
    assert out["konfidenz"] == (pytest.approx(expected_conf) if expected_conf is not None else None)
    assert out["quant_konsistenz"] == expected_kons
    assert not any(d.code == "vorschlag_unparsed" for d in diags)


def test_norm_vorschlag_unparsed_is_diagnosed():
    out, diags = tasks._norm_vorschlag({"vorschlag": {"foo": "bar"}}, held=False)
    assert out["typ"] == "INFO" and out["konfidenz"] is None
    assert "nicht im erwarteten Format" in out["begruendung"]
    assert [d.code for d in diags] == ["vorschlag_unparsed"] and "foo" in diags[0].message
    out2, diags2 = tasks._norm_vorschlag({}, held=True)
    assert out2["typ"] == "INFO" and diags2[0].code == "vorschlag_unparsed"
    # Unbekannter Typ, aber Begründung vorhanden → INFO mit Info-Diagnose
    out3, diags3 = tasks._norm_vorschlag({"vorschlag": {"typ": "xyz", "begruendung": "b", "evidenz": ["[1]", {"quelle": "News", "referenz": "[2]"}]}}, held=False)
    assert out3["typ"] == "INFO" and [d.code for d in diags3] == ["vorschlag_typ_unbekannt"]
    assert out3["evidenz"] == [{"quelle": "quant", "referenz": "[1]"}, {"quelle": "news", "referenz": "[2]"}]


def test_validate_synthesis_normalises_nested_enums():
    content = {
        "faktor_zusammenspiel": [{"muster": "M", "richtung": "verstärkend", "evidenz_urteil": "bestätigt", "regel_code": "value_trap"}],
        "quellen_zusammenhaenge": [{"quellen": ["News", "Insider-Transaktionen"], "befund": "B", "richtung": "bestätigend", "bedeutung": "4"}],
        "nicht_eingepreiste_themen": [{"thema": "T", "art": "Produktzyklus", "zeithorizont": "mittelfristig", "konfidenz": "60 %", "richtung": "bullish"}],
        "vorschlag": {"typ": "ÜBERPRÜFEN", "konfidenz": 0.5, "begruendung": "b"},
    }
    out, _ = tasks._validate_synthesis(content, [], held=False)
    assert out["faktor_zusammenspiel"][0]["richtung"] == "verstaerkend"
    assert out["faktor_zusammenspiel"][0]["evidenz_urteil"] == "bestaetigt"
    assert out["quellen_zusammenhaenge"][0]["quellen"] == ["insider", "news"]
    assert out["quellen_zusammenhaenge"][0]["bedeutung"] == 4
    t = out["nicht_eingepreiste_themen"][0]
    assert (t["art"], t["zeithorizont"], t["konfidenz"], t["richtung"]) == ("produktzyklus", "mittel", 0.6, "positiv")


def test_normalize_helpers():
    from app.core.analyst.normalize import canon, norm_enum, norm_float, pick

    assert canon("Einstieg prüfen!") == "einstieg_pruefen"
    assert norm_enum("Früh", ["frueh", "teilweise", "bereits"]) == "frueh"
    assert norm_enum("Zuliefer", ["direkt", "zulieferer"]) == "zulieferer"
    assert norm_enum("", ["a"]) is None and norm_enum("zzz", ["aaaa", "bbbb"]) is None
    assert norm_float("0,75") == 0.75 and norm_float("75%", 0, 1) == 0.75 and norm_float(80, 0, 1) == 0.8
    assert norm_float("1.234,5") == 1234.5 and norm_float("abc") is None and norm_float(True) is None
    assert pick({"Begründung": "x"}, "begruendung") == "x" and pick({}, "a", default=1) == 1


def test_synthesis_prompt_sections_12_13_koyfin_and_history(fake):
    from app.core.analyst import prompts

    user = prompts.build_synthesis_user_prompt(_item(held=False), [], {}, {"scoring_version": "v2"}, _hints(), {}, None, False,
                                               koyfin_text="**SAP — SAP SE**\n- P/E: 25,00", history_text="| Stand | Score |")
    assert "## 12. Koyfin-Kennzahlen (vollständig)\n**SAP — SAP SE**" in user
    assert "## 13. Verlauf (letzte Snapshots)\n| Stand | Score |" in user
    assert "Koyfin" in user.split("## 1.")[0]  # Aufgabentext verweist auf die Koyfin-Daten
    user2 = prompts.build_synthesis_user_prompt(_item(held=False), [], {}, {"scoring_version": "v2"}, _hints(), {}, None, False)
    assert "## 12. Koyfin-Kennzahlen (vollständig)\nnicht verfügbar" in user2
    f = fake([_load("ollama_synthesis_sample.json")])
    tasks.synthesize_company(_item(held=False), [], {}, {"scoring_version": "v2"}, _hints(), {}, None, Settings(), held=False,
                             scored=_scored(), period="p", koyfin_text="KOYFIN-BLOCK", history_text="VERLAUF-BLOCK")
    assert "KOYFIN-BLOCK" in f.calls[0]["user"] and "VERLAUF-BLOCK" in f.calls[0]["user"]
