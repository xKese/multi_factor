"""System-Prompt, JSON-Schemas und Prompt-Builder des KI-Analysten.

Alle Schemas sind JSON-Schema-Dicts (Ollama ``format``) mit deutschen
Keys. Die Builder erzeugen reine Textprompts; jede Quelle bekommt einen
nummerierten Abschnitt, fehlende Quellen werden ausdrücklich als „nicht
verfügbar“ markiert, damit das Modell nichts erfindet.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime

# --------------------------------------------------------------------------- #
# System-Prompt
# --------------------------------------------------------------------------- #
SYSTEM_PROMPT = """Du bist der KI-Analyst des Investment-Komitees für das Meeder & Seifer Multi-Faktor-Modell (Composite v2: Value, Quality, Momentum, Investment; Zonen KANDIDAT / HALTEN / VERKAUFEN / FILTER).

Regeln:
1. Du lieferst Entscheidungsunterstützung für das Komitee — keine Handelsaufträge, keine Anlageberatung.
2. Nur belegbare Aussagen: Jede Bewertung stützt sich auf das gelieferte Material (Quant-Daten, Transkript, News, Insider, Ereignisse). Zitiere wörtlich oder nenne die Referenz (z. B. "[3]" für News Nr. 3, "CFO im Call"). Erfinde keine Zahlen.
3. Ist die Datenlage dünn, schreibe ausdrücklich "unzureichend" und senke die Konfidenz.
4. Ziehe Zusammenhänge zwischen den Quellen: Widersprüche und Bestätigungen zwischen Quant-Daten, Management-Aussagen, News, Insider-Verhalten und Kursreaktion sind der Kern deiner Arbeit.
5. Das Feld factor_context enthält den Quant-Prior inklusive einer englischen Methodik-Beschreibung (composite_v2.methodology). Z-Scores sind peer-neutralisiert: > 0 besser als die Vergleichsgruppe, < 0 schlechter; Indikatoren mit direction = -1 sind invertiert (niedriger Rohwert = besser).
6. Antworte ausschließlich mit einem JSON-Objekt gemäß Schema, auf Deutsch, ohne Text davor oder danach.
"""

DISCLAIMER = (
    "Entscheidungsunterstützung des KI-Analysten — kein Handelsauftrag, keine "
    "Anlageberatung. Alle Aussagen sind gegen die Quellen zu prüfen."
)


# --------------------------------------------------------------------------- #
# Schema-Bausteine
# --------------------------------------------------------------------------- #
def _s(description: str = "") -> dict:
    d: dict = {"type": "string"}
    if description:
        d["description"] = description
    return d


def _enum(values: list[str], description: str = "") -> dict:
    d: dict = {"type": "string", "enum": list(values)}
    if description:
        d["description"] = description
    return d


def _num(lo: float | None = None, hi: float | None = None, description: str = "") -> dict:
    d: dict = {"type": "number"}
    if lo is not None:
        d["minimum"] = lo
    if hi is not None:
        d["maximum"] = hi
    if description:
        d["description"] = description
    return d


def _int(lo: int, hi: int, description: str = "") -> dict:
    d: dict = {"type": "integer", "minimum": lo, "maximum": hi}
    if description:
        d["description"] = description
    return d


def _bool() -> dict:
    return {"type": "boolean"}


def _arr(items: dict, description: str = "") -> dict:
    d: dict = {"type": "array", "items": items}
    if description:
        d["description"] = description
    return d


def _obj(props: dict, required: list[str] | None = None) -> dict:
    return {
        "type": "object",
        "properties": props,
        "required": list(required if required is not None else props.keys()),
    }


def _nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


URTEIL = ["bestaetigt", "widerspricht", "neutral"]
THEMA_ART = [
    "produktzyklus", "margenwende", "guidance_ton", "regulierung", "auftrag",
    "restrukturierung", "kapitalallokation", "sonstiges",
]
SUGGESTION_TYPES = [
    "EINSTIEG_PRÜFEN", "HALTEN", "ÜBERPRÜFEN", "REDUZIEREN_VERKAUF_PRÜFEN", "INFO",
]
EVIDENCE_SOURCES = [
    "transcript", "news", "insider", "earnings", "kalender", "quant", "kursreaktion",
    "vorherige_analyse", "zusammenspiel", "thema",
]
RELATIONS = ["zulieferer", "kunde", "wettbewerber", "partner", "sonstiges"]
HORIZONS = ["kurz", "mittel", "lang"]

_QUANT_ABGLEICH = _obj({"urteil": _enum(URTEIL), "begruendung": _s()})

_THEMEN_KANDIDATEN = _arr(
    _obj({"thema": _s(), "zitat": _s("Wörtliches Zitat oder Referenz"), "art": _enum(THEMA_ART)})
)

TRANSCRIPT_CHUNK_SCHEMA = _obj(
    {
        "kernaussagen": _arr(_s()),
        "guidance_hinweise": _arr(_s()),
        "risiken": _arr(_s()),
        "qa_highlights": _arr(_s()),
        "ton_hinweise": _arr(_s()),
        "themen_kandidaten": _THEMEN_KANDIDATEN,
        "antworten_prueffragen": _arr(_obj({"code": _s(), "befund": _s()})),
    }
)

TRANSCRIPT_SCHEMA = _obj(
    {
        "zusammenfassung": _s("3–6 Sätze"),
        "kernaussagen": _arr(_s()),
        "guidance": _obj(
            {
                "aenderung": _enum(["angehoben", "bestaetigt", "gesenkt", "keine_angabe"]),
                "details": _s(),
            }
        ),
        "ton": _int(1, 5, "1 = sehr defensiv, 5 = sehr zuversichtlich"),
        "vertrauen_management": _int(1, 5, "Glaubwürdigkeit/Konkretheit der Aussagen"),
        "glaubwuerdigkeit_flags": _arr(_s("z. B. vage Antworten, Ausweichen, Widersprüche")),
        "risiken": _arr(_s()),
        "qa_highlights": _arr(_s()),
        "quant_abgleich": _QUANT_ABGLEICH,
        "themen_kandidaten": _THEMEN_KANDIDATEN,
        "antworten_prueffragen": _arr(
            _obj({"code": _s(), "befund": _s(), "belegt": _bool()})
        ),
    }
)
TRANSCRIPT_REQUIRED = ("zusammenfassung", "guidance", "ton", "quant_abgleich")

NEWS_SCHEMA = _obj(
    {
        "zusammenfassung": _s("2–5 Sätze"),
        "themen": _arr(
            _obj(
                {
                    "titel": _s(),
                    "bewertung": _enum(["positiv", "negativ", "neutral"]),
                    "quellen": _arr({"type": "integer"}, "Nummern der News-Items"),
                }
            )
        ),
        "wesentlichkeit": _int(1, 5, "1 = Rauschen, 5 = thesenrelevant"),
        "quant_abgleich": _QUANT_ABGLEICH,
        "themen_kandidaten": _arr(
            _obj({"thema": _s(), "quellen": _arr({"type": "integer"}), "art": _enum(THEMA_ART)})
        ),
    }
)
NEWS_REQUIRED = ("zusammenfassung", "themen", "wesentlichkeit")

SYNTHESIS_SCHEMA = _obj(
    {
        "faktor_zusammenspiel": _arr(
            _obj(
                {
                    "muster": _s(),
                    "faktoren": _arr(_s()),
                    "richtung": _enum(["verstaerkend", "widerspruechlich", "neutral"]),
                    "interpretation": _s(),
                    "evidenz_urteil": _enum(["bestaetigt", "widerspricht", "keine_evidenz"]),
                    "belege": _arr(_s()),
                    "regel_code": _nullable(_s("Code des regelbasierten Hinweises oder null")),
                }
            )
        ),
        "quellen_zusammenhaenge": _arr(
            _obj(
                {
                    "quellen": _arr(_enum(EVIDENCE_SOURCES), "mindestens zwei verschiedene"),
                    "befund": _s(),
                    "richtung": _enum(["bestaetigend", "widerspruechlich", "neutral"]),
                    "bedeutung": _int(1, 5),
                    "belege": _arr(_s()),
                }
            )
        ),
        "nicht_eingepreiste_themen": _arr(
            _obj(
                {
                    "thema": _s(),
                    "art": _enum(THEMA_ART),
                    "evidenz": _arr(_s()),
                    "warum_nicht_eingepreist": _s("mit Zahlen aus Quant-Daten/Kursreaktion"),
                    "zeithorizont": _enum(HORIZONS),
                    "konfidenz": _num(0, 1),
                    "bestaetigung_durch": _s("Was würde die These bestätigen?"),
                    "richtung": _enum(["positiv", "negativ"]),
                }
            )
        ),
        "verbundene_unternehmen": _arr(
            _obj(
                {
                    "name": _s(),
                    "ticker": _nullable(_s()),
                    "beziehung": _enum(RELATIONS),
                    "kontext": _s(),
                    "quelle": _s(),
                }
            )
        ),
        "vorschlag": _obj(
            {
                "typ": _enum(SUGGESTION_TYPES),
                "konfidenz": _num(0, 1),
                "begruendung": _s("3–5 Sätze, mit Bezug auf Quellen"),
                "evidenz": _arr(_obj({"quelle": _enum(EVIDENCE_SOURCES), "referenz": _s()})),
                "quant_konsistenz": _enum(URTEIL),
            }
        ),
    }
)
SYNTHESIS_REQUIRED = ("faktor_zusammenspiel", "quellen_zusammenhaenge", "nicht_eingepreiste_themen", "vorschlag")

THEMES_SCHEMA = _obj(
    {
        "themen": _arr(
            _obj(
                {
                    "titel": _s(),
                    "beschreibung": _s(),
                    "art": _enum(["sektor", "region", "makro", "firmenspezifisch"]),
                    "richtung": _enum(["positiv", "negativ", "gemischt"]),
                    "betroffene_uids": _arr(_s()),
                    "sektoren": _arr(_s()),
                    "regionen": _arr(_s()),
                    "konfidenz": _num(0, 1),
                    "bestaetigung_durch": _s(),
                    "belege": _arr(_s()),
                }
            )
        ),
        "verknuepfungen": _arr(
            _obj({"uid_a": _s(), "uid_b": _s(), "beziehung": _enum(RELATIONS), "beleg": _s()})
        ),
    }
)
THEMES_REQUIRED = ("themen",)

TREND_ART = [
    "technologie", "energie_transport", "industrie", "makro", "finanzmaerkte",
    "m_and_a", "life_sciences", "regulierung", "konsum", "sonstiges",
]
_TREND_BASE = {
    "titel": _s(),
    "beschreibung": _s("2–4 Sätze: Was passiert, warum jetzt?"),
    "art": _enum(TREND_ART),
    "zeithorizont": _enum(HORIZONS),
    "staerke": _int(1, 5),
    "konfidenz": _num(0, 1),
    "evidenz": _arr(_obj({"quelle": _enum(["news", "transcript", "thema"]), "referenz": _s()})),
    "betroffene_industriegruppen": _arr(_s("Namen aus dem Industriegruppen-Profil")),
    "verlierer_hinweis": _nullable(_s()),
}
_PROFITEUR = _obj(
    {
        "uid": _s("exakt aus der Kandidatentabelle kopieren"),
        "ticker": _s(),
        "rolle": _enum(["direkt", "zulieferer", "enabler", "picks_and_shovels", "zweite_ordnung"]),
        "begruendungskette": _arr(_s("Trend → Mechanismus → Firma, je Schritt ein Satz")),
        "evidenz": _arr(_s()),
        "eingepreist": _enum(["frueh", "teilweise", "bereits"]),
        "eingepreist_begruendung": _s("mit z_momentum, eps_rev_3m, mom_12_1, z_value argumentieren"),
        "vorgeschlagene_aktion": _enum(["Kandidat prüfen", "Watchlist", "Position prüfen", "Nur Information"]),
    }
)
TREND_GROUPS_SCHEMA = _obj({"trends": _arr(_obj(_TREND_BASE))})
TREND_SCHEMA = _obj({"trends": _arr(_obj({**_TREND_BASE, "profiteure": _arr(_PROFITEUR)}))})
TREND_REQUIRED = ("trends",)


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def _json_default(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    return str(obj)


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=1, default=_json_default)


def prompt_hash(system: str, user: str) -> str:
    return hashlib.sha256((system + "\n␞\n" + user).encode("utf-8")).hexdigest()[:16]


def _header(item, held: bool) -> str:
    status = "im Bestand (aktives Portfolio)" if held else "nicht im Bestand"
    zone = getattr(item, "zone", None) or "–"
    score = getattr(item, "composite_score", None)
    score_txt = f"{score:.1f}".replace(".", ",") if isinstance(score, (int, float)) else "–"
    return (
        f"Titel: {item.ticker} — {item.name} (uid {item.uid}), {status}; "
        f"Quant-Zone {zone}, Composite {score_txt}/100."
    )


def _section(n: int, title: str, body: str | None) -> str:
    body = body.strip() if isinstance(body, str) and body.strip() else "nicht verfügbar"
    return f"## {n}. {title}\n{body}\n"


# --------------------------------------------------------------------------- #
# Transkript
# --------------------------------------------------------------------------- #
def build_transcript_user_prompt(
    item, factor_context: dict, text: str, part_label: str, hints, held: bool = False
) -> str:
    from .factor_interplay import hints_to_prompt

    questions = [h for h in hints if getattr(h, "text_question", None)]
    q_text = "\n".join(f"- [{h.code}] {h.text_question}" for h in questions) or "keine"
    return (
        f"{_header(item, held)}\n\n"
        f"Aufgabe: Werte den Earnings-Call ({part_label}) aus. Liefere Kernaussagen, "
        "Guidance-Änderung, Ton (1–5), Vertrauen ins Management (1–5), Glaubwürdigkeits-"
        "Flags, Risiken, Q&A-Highlights, Themenkandidaten (mit Zitat) und den Abgleich "
        "mit dem Quant-Prior. Beantworte außerdem die Prüffragen (Code angeben, "
        "belegt = true nur bei konkretem Beleg im Text).\n\n"
        + _section(1, "Quant-Prior (factor_context)", _dump(factor_context))
        + _section(2, "Regelbasierte Hinweise zum Faktor-Zusammenspiel", hints_to_prompt(hints))
        + _section(3, "Prüffragen", q_text)
        + _section(4, f"Transkript — {part_label}", text)
    )


def build_reduce_prompt(item, factor_context: dict, chunk_results: list[dict], hints, held: bool = False) -> str:
    from .factor_interplay import hints_to_prompt

    parts = "\n\n".join(f"### Teil {i}\n{_dump(c)}" for i, c in enumerate(chunk_results, 1))
    return (
        f"{_header(item, held)}\n\n"
        "Aufgabe: Fasse die Teilauswertungen des Earnings-Calls zu einer Gesamtauswertung "
        "gemäß Schema zusammen (Guidance, Ton, Vertrauen, Risiken, Q&A, Themenkandidaten, "
        "Prüffragen-Antworten, Quant-Abgleich). Keine neuen Fakten erfinden.\n\n"
        + _section(1, "Quant-Prior (factor_context)", _dump(factor_context))
        + _section(2, "Regelbasierte Hinweise", hints_to_prompt(hints))
        + _section(3, "Teilauswertungen", parts)
    )


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
def render_news_items(items: list[dict]) -> str:
    lines = []
    for n, it in enumerate(items, 1):
        t = it.get("time") or it.get("time_published")
        if isinstance(t, datetime):
            t = t.strftime("%Y-%m-%d %H:%M")
        sent = it.get("sentiment", it.get("ticker_sentiment_score"))
        sent_txt = f"{sent:+.2f}" if isinstance(sent, (int, float)) else "–"
        rel = it.get("relevance")
        rel_txt = f"{rel:.2f}" if isinstance(rel, (int, float)) else "–"
        lines.append(
            f"[{n}] {t or '–'} · {it.get('source') or '–'} · {it.get('title')} "
            f"(Sentiment {sent_txt}, Relevanz {rel_txt})\n    {(it.get('summary') or '')[:400]}"
        )
    return "\n".join(lines)


def build_news_user_prompt(item, factor_context: dict, news_items: list[dict], held: bool = False) -> str:
    return (
        f"{_header(item, held)}\n\n"
        "Aufgabe: Fasse die News zusammen, bündle sie zu Themen (mit Quellen-Nummern), "
        "bewerte die Wesentlichkeit (1–5) für die Investmentthese, gleiche mit dem "
        "Quant-Prior ab und nenne Themenkandidaten, die der Markt noch nicht "
        "verarbeitet haben könnte.\n\n"
        + _section(1, "Quant-Prior (factor_context)", _dump(factor_context))
        + _section(2, "News-Items", render_news_items(news_items))
    )


# --------------------------------------------------------------------------- #
# Synthese (alle Quellen)
# --------------------------------------------------------------------------- #
def _events_text(events) -> str:
    lines = []
    for e in events or []:
        d = e.event_date.isoformat() if hasattr(e, "event_date") and e.event_date else ""
        lines.append(f"- [{getattr(e, 'kind', '')}] {d} {getattr(e, 'title', '')}")
    return "\n".join(lines)


def _insider_text(event) -> str:
    if event is None:
        return ""
    p = event.payload or {}
    lines = [
        f"{p.get('n_buys', 0)} Käufe, {p.get('n_sells', 0)} Verkäufe, netto "
        f"{p.get('net_shares', 0)} Aktien."
    ]
    for t in (p.get("transactions") or [])[:12]:
        lines.append(
            f"- {t.get('date')} {t.get('type')} {t.get('shares')} Stk. @ {t.get('price')} — "
            f"{t.get('executive')} ({t.get('title')})"
        )
    return "\n".join(lines)


def _earnings_text(event) -> str:
    if event is None:
        return ""
    p = event.payload or {}
    return (
        f"{event.title}\nEPS berichtet {p.get('reported_eps')}, Schätzung {p.get('estimated_eps')}, "
        f"Überraschung {p.get('surprise')} ({p.get('surprise_pct')} %), Fiskalquartal bis "
        f"{p.get('fiscal_date_ending')}."
    )


def _previous_text(prev: dict | None) -> str:
    if not prev or not isinstance(prev.get("content"), dict):
        return ""
    c = prev["content"]
    v = c.get("vorschlag") or {}
    themes = [t.get("thema") for t in c.get("nicht_eingepreiste_themen") or [] if isinstance(t, dict)]
    links = [z.get("befund") for z in c.get("quellen_zusammenhaenge") or [] if isinstance(z, dict)]
    created = prev.get("created_at")
    created = created.strftime("%Y-%m-%d") if isinstance(created, datetime) else str(created or "")
    return (
        f"Analyse vom {created}: Vorschlag {v.get('typ')} (Konfidenz {v.get('konfidenz')}) — "
        f"{v.get('begruendung')}\nOffene Themen: {'; '.join(t for t in themes if t) or '–'}\n"
        f"Zusammenhänge: {'; '.join(l for l in links if l) or '–'}"
    )


def build_synthesis_user_prompt(
    item,
    factor_context: dict,
    events: list,
    insights: dict,
    hints,
    market_reaction: dict,
    previous_synthesis: dict | None,
    held: bool,
    calendar_event=None,
) -> str:
    """Synthese-Prompt mit **allen** Quellen als nummerierte Abschnitte."""
    from .factor_interplay import hints_to_prompt

    by_kind = {}
    for e in events or []:
        by_kind.setdefault(e.kind, e)
    transcript = insights.get("transcript_summary")
    news = insights.get("news_digest")
    news_event = by_kind.get("news")
    news_raw = render_news_items((news_event.payload or {}).get("items") or []) if news_event else ""
    upcoming = calendar_event or by_kind.get("earnings_upcoming")

    task = (
        "Aufgabe: Erstelle die Synthese über ALLE folgenden Quellen. Pflicht:\n"
        "- faktor_zusammenspiel: Adressiere jeden regelbasierten Hinweis (regel_code setzen) "
        "und ergänze eigene Muster (regel_code null). Prüfe je Muster, ob Transkript/News "
        "die Quant-Lesart bestätigen oder widerlegen.\n"
        "- quellen_zusammenhaenge: Verbinde ausdrücklich mindestens zwei verschiedene Quellen "
        "je Eintrag (z. B. Insider-Käufe nach dem Call bei negativen Revisionen; News-Thema "
        "deckt sich mit Capex-Aussage, Kurs reagiert nicht). Benenne Widersprüche.\n"
        "- nicht_eingepreiste_themen: Prüfe jeden Themenkandidaten gegen Bewertungs-Z, "
        "12-1-Momentum, EPS-Revisionstrend, News-Sentiment-Trend und Kursreaktion seit dem "
        "Bericht; zitiere die Zahlen in warum_nicht_eingepreist. Nur Themen, die der Markt "
        "nach diesen Daten noch nicht verarbeitet hat.\n"
        "- verbundene_unternehmen: Zulieferer, Kunden, Wettbewerber, Partner, die im Call "
        "oder in den News genannt werden (Ticker nur, wenn sicher).\n"
        "- vorschlag: EINSTIEG_PRÜFEN nur für Titel außerhalb des Bestands; HALTEN und "
        "REDUZIEREN_VERKAUF_PRÜFEN nur für Bestandstitel; sonst ÜBERPRÜFEN oder INFO. "
        "Begründung mit Quellenbezug; quant_konsistenz = Verhältnis zum Quant-Prior.\n"
        "- Hat sich gegenüber der vorherigen Analyse etwas geändert? Dann benenne es.\n"
    )
    return (
        f"{_header(item, held)}\n\n{task}\n"
        + _section(1, "Quant-Prior (factor_context)", _dump(factor_context))
        + _section(2, "Markt-Reaktion / bereits verarbeitet", _dump(market_reaction) if market_reaction else "")
        + _section(3, "Regelbasierte Hinweise zum Faktor-Zusammenspiel", hints_to_prompt(hints))
        + _section(4, "Earnings-Überraschung", _earnings_text(by_kind.get("earnings")))
        + _section(5, "Nächster Berichtstermin", upcoming.title if upcoming is not None else "")
        + _section(
            6, "Earnings-Call-Auswertung",
            _dump(transcript.get("content")) if transcript and transcript.get("content") else "",
        )
        + _section(7, "News-Digest", _dump(news.get("content")) if news and news.get("content") else "")
        + _section(8, "News-Items (roh)", news_raw)
        + _section(9, "Insider-Transaktionen", _insider_text(by_kind.get("insider")))
        + _section(10, "Ereignisse seit dem letzten Lauf", _events_text(events))
        + _section(11, "Vorherige Analyse", _previous_text(previous_synthesis))
    )


# --------------------------------------------------------------------------- #
# Themen (Run-Ebene)
# --------------------------------------------------------------------------- #
def build_themes_user_prompt(run_id: str, company_themes: list[dict], company_links: list[dict]) -> str:
    themes = "\n".join(
        f"- {t.get('ticker')} ({t.get('uid')}, {t.get('sector')}, {t.get('region')}): "
        f"{t.get('thema')} [{t.get('art')}, {t.get('richtung')}, Konfidenz {t.get('konfidenz')}]"
        for t in company_themes
    )
    links = "\n".join(
        f"- {l.get('ticker')} ({l.get('uid')}) → {l.get('related_name')}"
        f"{' (' + l['related_uid'] + ')' if l.get('related_uid') else ''}: {l.get('relation')} — {l.get('context')}"
        for l in company_links
    )
    return (
        f"Lauf {run_id}. Aufgabe: Clustere die firmenbezogenen Themen zu übergreifenden "
        "Themen (Sektor, Region, Makro oder firmenspezifisch), nenne betroffene uids, "
        "Richtung, Konfidenz und was das Thema bestätigen würde. Leite aus den "
        "Unternehmensverknüpfungen firmenübergreifende Zusammenhänge ab (verknuepfungen; "
        "nur uids aus der Liste).\n\n"
        + _section(1, "Themen je Firma", themes)
        + _section(2, "Unternehmensverknüpfungen je Firma", links)
    )


# --------------------------------------------------------------------------- #
# Trend-Scan
# --------------------------------------------------------------------------- #
def _trend_task(max_trends: int) -> str:
    return (
        f"Aufgabe: Identifiziere 3–{max_trends} aktuelle Trends aus den Markt-News und den "
        "Universums-Themen. Je Trend: Beschreibung, Art, Zeithorizont, Stärke, Konfidenz, "
        "Evidenz (News-Nummern [n], Transkript-/Themen-Referenzen) und die betroffenen "
        "Industriegruppen — inklusive Zulieferer- und Enabler-Gruppen (zweite Ordnung), "
        "nicht nur die offensichtlichen.\n"
    )


def _profiteure_task(max_beneficiaries: int) -> str:
    return (
        f"Ordne je Trend bis zu {max_beneficiaries} Profiteure AUSSCHLIESSLICH aus der "
        "Kandidatentabelle zu (uid exakt kopieren). Rollen: direkt, zulieferer, enabler, "
        "picks_and_shovels, zweite_ordnung. Begründe als Kette Trend → Mechanismus → Firma. "
        "Beurteile anhand z_momentum, eps_rev_3m, mom_12_1 und z_value, ob der Markt den "
        "Effekt schon eingepreist hat (frueh / teilweise / bereits) — früh erkennbare "
        "Profiteure sind das Ziel. Nenne je Trend ggf. Verlierer als Hinweis.\n"
    )


def build_trend_prompt_a(news_text: str, run_themes: list[dict], group_profile_text: str, settings) -> str:
    themes = "\n".join(
        f"- {t.get('title')} [{t.get('kind')}, {t.get('direction')}]: {t.get('description')}"
        for t in run_themes or []
    )
    return (
        _trend_task(int(getattr(settings, "analyst_trend_max_trends", 6)))
        + "\n"
        + _section(1, "Markt-News", news_text)
        + _section(2, "Themen aus den Firmenanalysen dieses Laufs", themes)
        + _section(3, "Industriegruppen-Profil des Universums", group_profile_text)
    )


def build_trend_prompt_b(trends_a: list[dict], candidate_text: str, settings) -> str:
    return (
        "Die Trends wurden bereits identifiziert (Abschnitt 1). "
        + _profiteure_task(int(getattr(settings, "analyst_trend_max_beneficiaries", 8)))
        + "Übernimm Titel, Beschreibung, Art, Zeithorizont, Stärke, Konfidenz, Evidenz und "
        "Industriegruppen unverändert.\n\n"
        + _section(1, "Trends", _dump(trends_a))
        + _section(2, "Kandidatentabelle (Universum, gefiltert)", candidate_text)
    )


def build_trend_prompt_single(
    news_text: str, run_themes: list[dict], group_profile_text: str, candidate_text: str, settings
) -> str:
    themes = "\n".join(
        f"- {t.get('title')} [{t.get('kind')}, {t.get('direction')}]: {t.get('description')}"
        for t in run_themes or []
    )
    return (
        _trend_task(int(getattr(settings, "analyst_trend_max_trends", 6)))
        + _profiteure_task(int(getattr(settings, "analyst_trend_max_beneficiaries", 8)))
        + "\n"
        + _section(1, "Markt-News", news_text)
        + _section(2, "Themen aus den Firmenanalysen dieses Laufs", themes)
        + _section(3, "Industriegruppen-Profil des Universums", group_profile_text)
        + _section(4, "Kandidatentabelle (Universum, gefiltert)", candidate_text)
    )
