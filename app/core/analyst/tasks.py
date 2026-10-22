"""LLM-Tasks des KI-Analysten (Ollama Cloud).

Jede Funktion baut den Prompt, ruft :func:`ollama_client.chat_json` auf,
validiert das Ergebnis und liefert ein :class:`InsightResult` — nie eine
Exception wegen ungültigem JSON (Status ``unparsed`` mit Rohtext).
Transport-/Auth-Fehler (:class:`OllamaError`) werden durchgereicht; der
Runner entscheidet, ob der Lauf als ``partial`` endet.

``ollama`` ist ein Modulattribut, damit Tests den Client ersetzen können.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from ..diagnostics import SEV_INFO, SEV_WARNING, Diagnostic
from ..uid import slugify_name
from . import ollama_client, prompts, trend_scan
from .normalize import norm_enum, norm_float, pick
from .factor_interplay import InterplayHint

ollama = ollama_client

KIND_TRANSCRIPT = "transcript_summary"
KIND_NEWS = "news_digest"
KIND_SYNTHESIS = "synthesis"
KIND_THEMES = "themes"
KIND_TREND = "trend"


@dataclass
class InsightResult:
    kind: str
    status: str  # ok | unparsed | error
    content: dict | None
    raw_text: str
    n_calls: int
    model: str
    prompt_hash: str = ""
    period: str | None = None
    diagnostics: list[Diagnostic] = field(default_factory=list)


@dataclass
class SynthesisResult:
    insight: InsightResult
    suggestion: dict | None
    links: list[dict] = field(default_factory=list)


@dataclass
class ThemesResult:
    insight: InsightResult | None
    themes: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    n_calls: int = 0


@dataclass
class TrendScanResult:
    insight: InsightResult | None
    trends: list[dict] = field(default_factory=list)
    links: list[dict] = field(default_factory=list)
    n_calls: int = 0
    diagnostics: list[Diagnostic] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Aufruf-Helfer
# --------------------------------------------------------------------------- #
def _model(settings, model: str | None = None) -> str:
    return (model or getattr(settings, "analyst_model", "") or "gpt-oss:120b").strip()


def _call(user: str, schema: dict, required, settings, model: str | None = None):
    return ollama.chat_json(
        prompts.SYSTEM_PROMPT,
        user,
        schema,
        required,
        model=_model(settings, model),
        temperature=0.0,
        timeout=int(getattr(settings, "analyst_llm_timeout_seconds", 300)),
    )


def _result(kind: str, parsed: dict | None, raw: str, n: int, settings, model, user: str, period=None) -> InsightResult:
    return InsightResult(
        kind=kind,
        status="ok" if parsed is not None else "unparsed",
        content=parsed,
        raw_text=raw,
        n_calls=n,
        model=_model(settings, model),
        prompt_hash=prompts.prompt_hash(prompts.SYSTEM_PROMPT, user),
        period=period,
    )


# --------------------------------------------------------------------------- #
# Transkript
# --------------------------------------------------------------------------- #
def split_transcript(segments: list[dict]) -> tuple[list[dict], list[dict]]:
    """Prepared Remarks vs. Q&A: Q&A beginnt beim ersten Segment ab Index 1,
    dessen Titel „Analyst“ enthält oder bei einem Operator-Segment, auf das
    ein Analyst folgt."""
    for i in range(1, len(segments)):
        title = (segments[i].get("title") or "").lower()
        speaker = (segments[i].get("speaker") or "").lower()
        if "analyst" in title:
            return segments[:i], segments[i:]
        if speaker == "operator" and i + 1 < len(segments):
            nxt = (segments[i + 1].get("title") or "").lower()
            if "analyst" in nxt:
                return segments[:i], segments[i:]
    return segments, []


def render_segments(segments: list[dict]) -> str:
    lines = []
    for s in segments:
        who = s.get("speaker") or "?"
        title = s.get("title")
        head = f"{who} ({title})" if title else who
        lines.append(f"{head}: {s.get('content', '')}")
    return "\n".join(lines)


def chunk_segments(segments: list[dict], max_chars: int) -> list[list[dict]]:
    """Segmente zu Blöcken ≤ ``max_chars`` (an Segmentgrenzen)."""
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    size = 0
    for s in segments:
        n = len(s.get("content", "")) + len(s.get("speaker", "")) + 4
        if cur and size + n > max_chars:
            chunks.append(cur)
            cur, size = [], 0
        cur.append(s)
        size += n
    if cur:
        chunks.append(cur)
    return chunks


def summarize_transcript(
    item, transcript: dict, factor_context: dict, hints: list[InterplayHint], settings,
    held: bool = False, model: str | None = None,
) -> InsightResult:
    segments = transcript.get("segments") or []
    quarter = str(transcript.get("quarter") or "")
    max_chars = int(getattr(settings, "analyst_transcript_max_chars", 60000))
    total = sum(len(s.get("content", "")) for s in segments)
    if total <= max_chars:
        prepared, qa = split_transcript(segments)
        text = "### Prepared Remarks\n" + render_segments(prepared)
        if qa:
            text += "\n\n### Q&A\n" + render_segments(qa)
        user = prompts.build_transcript_user_prompt(item, factor_context, text, f"Quartal {quarter}", hints, held)
        parsed, raw, n = _call(user, prompts.TRANSCRIPT_SCHEMA, prompts.TRANSCRIPT_REQUIRED, settings, model)
        return _result(KIND_TRANSCRIPT, parsed, raw, n, settings, model, user, quarter)

    # Map-Reduce: Teile einzeln auswerten, dann zusammenführen.
    chunks = chunk_segments(segments, max_chars)
    results: list[dict] = []
    n_total = 0
    last_raw = ""
    for i, chunk in enumerate(chunks, 1):
        user = prompts.build_transcript_user_prompt(
            item, factor_context, render_segments(chunk), f"Quartal {quarter}, Teil {i}/{len(chunks)}", hints, held
        )
        parsed, raw, n = _call(user, prompts.TRANSCRIPT_CHUNK_SCHEMA, ("kernaussagen",), settings, model)
        n_total += n
        last_raw = raw
        if parsed is not None:
            results.append(parsed)
    if not results:
        return InsightResult(KIND_TRANSCRIPT, "unparsed", None, last_raw, n_total, _model(settings, model), "", quarter)
    user = prompts.build_reduce_prompt(item, factor_context, results, hints, held)
    parsed, raw, n = _call(user, prompts.TRANSCRIPT_SCHEMA, prompts.TRANSCRIPT_REQUIRED, settings, model)
    res = _result(KIND_TRANSCRIPT, parsed, raw, n_total + n, settings, model, user, quarter)
    return res


# --------------------------------------------------------------------------- #
# News
# --------------------------------------------------------------------------- #
def digest_news(
    item, news_items: list[dict], factor_context: dict, settings, held: bool = False,
    model: str | None = None, period: str | None = None,
) -> InsightResult:
    user = prompts.build_news_user_prompt(item, factor_context, news_items, held)
    parsed, raw, n = _call(user, prompts.NEWS_SCHEMA, prompts.NEWS_REQUIRED, settings, model)
    return _result(KIND_NEWS, parsed, raw, n, settings, model, user, period)


# --------------------------------------------------------------------------- #
# Synthese
# --------------------------------------------------------------------------- #
def resolve_company_mentions(mentions: list[dict], scored: pd.DataFrame | None, self_uid: str) -> list[dict]:
    """Genannte Firmen auf Universums-uids abbilden: exakter Ticker →
    exakter Namens-Slug → eindeutiger Slug-Prefix (beide Richtungen);
    mehrdeutig → ohne uid."""
    out: list[dict] = []
    has_df = scored is not None and not scored.empty and "uid" in scored.columns
    if has_df:
        tickers = scored["ticker"].astype(str).str.upper() if "ticker" in scored.columns else None
        slugs = scored["name"].map(slugify_name) if "name" in scored.columns else None
    for m in mentions or []:
        if not isinstance(m, dict):
            continue
        name = str(m.get("name") or "").strip()
        ticker = str(m.get("ticker") or "").strip().upper()
        if not name and not ticker:
            continue
        related_uid = None
        if has_df:
            if ticker and tickers is not None:
                hits = scored[tickers == ticker]
                if len(hits) == 1:
                    related_uid = str(hits.iloc[0]["uid"])
            if related_uid is None and name and slugs is not None:
                slug = slugify_name(name)
                if slug:
                    exact = scored[slugs == slug]
                    if len(exact) == 1:
                        related_uid = str(exact.iloc[0]["uid"])
                    elif len(exact) == 0 and len(slug) >= 4:
                        pref = scored[slugs.str.startswith(slug) | slugs.map(lambda s: bool(s) and slug.startswith(s) and len(s) >= 4)]
                        if len(pref) == 1:
                            related_uid = str(pref.iloc[0]["uid"])
        if related_uid == self_uid:
            continue
        relation = str(m.get("beziehung") or "sonstiges").lower()
        if relation not in prompts.RELATIONS:
            relation = "sonstiges"
        out.append(
            {
                "related_uid": related_uid,
                "related_name": name or ticker,
                "related_ticker": ticker or None,
                "relation": relation,
                "context": str(m.get("kontext") or "")[:400],
                "source": str(m.get("quelle") or "")[:120],
            }
        )
    return out


SUGGESTION_ALIASES = {
    "einstieg": "EINSTIEG_PRÜFEN", "kaufen": "EINSTIEG_PRÜFEN", "kauf": "EINSTIEG_PRÜFEN",
    "buy": "EINSTIEG_PRÜFEN", "aufbauen": "EINSTIEG_PRÜFEN",
    "halten": "HALTEN", "hold": "HALTEN", "beibehalten": "HALTEN",
    "ueberpruefen": "ÜBERPRÜFEN", "pruefen": "ÜBERPRÜFEN", "review": "ÜBERPRÜFEN", "beobachten": "ÜBERPRÜFEN",
    "reduzieren": "REDUZIEREN_VERKAUF_PRÜFEN", "verkaufen": "REDUZIEREN_VERKAUF_PRÜFEN",
    "verkauf": "REDUZIEREN_VERKAUF_PRÜFEN", "sell": "REDUZIEREN_VERKAUF_PRÜFEN", "abbauen": "REDUZIEREN_VERKAUF_PRÜFEN",
    "info": "INFO", "information": "INFO", "keine": "INFO", "neutral": "INFO",
}
URTEIL_ALIASES = {
    "bestaetigend": "bestaetigt", "konsistent": "bestaetigt", "stuetzt": "bestaetigt", "confirms": "bestaetigt",
    "widerspruechlich": "widerspricht", "inkonsistent": "widerspricht", "contradicts": "widerspricht",
    "unklar": "neutral", "gemischt": "neutral",
}


def _norm_vorschlag(content: dict, held: bool) -> tuple[dict, list[Diagnostic]]:
    """``vorschlag`` tolerant lesen; bei unverwertbarem Block Diagnose statt
    stiller Defaults."""
    diags: list[Diagnostic] = []
    raw = pick(content, "vorschlag", "empfehlung", "recommendation", "suggestion")
    if isinstance(raw, str):
        raw = {"typ": raw}
    v: dict = dict(raw) if isinstance(raw, dict) else {}
    typ_raw = pick(v, "typ", "type", "vorschlag_typ", "empfehlung", "aktion", "action", "kategorie")
    typ = norm_enum(typ_raw, prompts.SUGGESTION_TYPES, SUGGESTION_ALIASES)
    conf = norm_float(pick(v, "konfidenz", "confidence", "sicherheit", "wahrscheinlichkeit"), 0.0, 1.0)
    begr = pick(v, "begruendung", "begründung", "rationale", "reasoning", "erklaerung", "erklärung", "text", default="")
    begr = str(begr or "").strip()
    kons = norm_enum(
        pick(v, "quant_konsistenz", "konsistenz", "quant_abgleich", "consistency"),
        prompts.URTEIL, URTEIL_ALIASES,
    )
    if isinstance(pick(v, "quant_konsistenz", "konsistenz", "quant_abgleich"), dict):
        kons = norm_enum(pick(pick(v, "quant_konsistenz", "konsistenz", "quant_abgleich"), "urteil"), prompts.URTEIL, URTEIL_ALIASES)
    problems = []
    if typ is None:
        problems.append(f"typ={typ_raw!r}")
    if not begr:
        problems.append("begruendung fehlt")
    if typ is None and not begr:
        keys = ", ".join(sorted(str(k) for k in (v.keys() if v else content.keys())))[:200]
        diags.append(
            Diagnostic(
                SEV_WARNING,
                "vorschlag_unparsed",
                f"Vorschlag vom Modell nicht im erwarteten Format ({'; '.join(problems)}; Keys: {keys}).",
            )
        )
        typ = "INFO"
        begr = "Vorschlag vom Modell nicht im erwarteten Format — Rohantwort über "
        begr += "`python -m app.tools.analyst inspect --uid <uid> --raw` prüfen."
    elif typ is None:
        diags.append(Diagnostic(SEV_INFO, "vorschlag_typ_unbekannt", f"Vorschlagstyp {typ_raw!r} nicht zuordenbar — als INFO geführt."))
        typ = "INFO"
    if held and typ == "EINSTIEG_PRÜFEN":
        typ = "ÜBERPRÜFEN"
    if not held and typ in ("HALTEN", "REDUZIEREN_VERKAUF_PRÜFEN"):
        typ = "ÜBERPRÜFEN"
    evid = pick(v, "evidenz", "evidence", "belege", default=[]) or []
    if isinstance(evid, dict):
        evid = [evid]
    ev_out = []
    for e in evid:
        if isinstance(e, dict):
            ev_out.append({"quelle": norm_enum(pick(e, "quelle", "source"), prompts.EVIDENCE_SOURCES) or "quant",
                           "referenz": str(pick(e, "referenz", "reference", "beleg", default="") or "")})
        elif isinstance(e, str) and e.strip():
            ev_out.append({"quelle": "quant", "referenz": e.strip()})
    out = {
        **v,
        "typ": typ,
        "konfidenz": conf,
        "begruendung": begr,
        "quant_konsistenz": kons or "neutral",
        "evidenz": ev_out,
        "disclaimer": prompts.DISCLAIMER,
    }
    return out, diags


def _validate_synthesis(content: dict, hints: list[InterplayHint], held: bool) -> tuple[dict, list[Diagnostic]]:
    diags: list[Diagnostic] = []
    # Faktor-Zusammenspiel: jeder Hinweis muss adressiert sein.
    fz = []
    for x in content.get("faktor_zusammenspiel") or []:
        if not isinstance(x, dict):
            continue
        x["richtung"] = norm_enum(x.get("richtung"), ["verstaerkend", "widerspruechlich", "neutral"]) or "neutral"
        x["evidenz_urteil"] = norm_enum(x.get("evidenz_urteil"), ["bestaetigt", "widerspricht", "keine_evidenz"], URTEIL_ALIASES) or "keine_evidenz"
        fz.append(x)
    seen = {str(x.get("regel_code")) for x in fz if x.get("regel_code")}
    for h in hints:
        if h.code not in seen:
            fz.append(
                {
                    "muster": h.label,
                    "faktoren": list(h.factors),
                    "richtung": "neutral",
                    "interpretation": h.hint,
                    "evidenz_urteil": "keine_evidenz",
                    "belege": [],
                    "regel_code": h.code,
                }
            )
            diags.append(Diagnostic(SEV_INFO, "interplay_unaddressed", f"Hinweis {h.code} vom LLM nicht adressiert — als „keine Evidenz“ ergänzt."))
    content["faktor_zusammenspiel"] = fz
    # Quellen-Zusammenhänge: mindestens zwei verschiedene Quellen.
    qz = []
    for z in content.get("quellen_zusammenhaenge") or []:
        if not isinstance(z, dict):
            continue
        quellen = [norm_enum(q, prompts.EVIDENCE_SOURCES) or str(q) for q in (z.get("quellen") or []) if q]
        if len(set(quellen)) < 2:
            continue
        z["quellen"] = sorted(set(quellen))
        z["richtung"] = norm_enum(z.get("richtung"), ["bestaetigend", "widerspruechlich", "neutral"], URTEIL_ALIASES) or "neutral"
        bed = norm_float(z.get("bedeutung"))
        z["bedeutung"] = int(max(1, min(5, round(bed)))) if bed is not None else 3
        qz.append(z)
    content["quellen_zusammenhaenge"] = qz
    # Themen: Konfidenz/Enums tolerant.
    themes = []
    for t in content.get("nicht_eingepreiste_themen") or []:
        if not isinstance(t, dict) or not t.get("thema"):
            continue
        conf = norm_float(t.get("konfidenz"), 0.0, 1.0)
        t["konfidenz"] = conf if conf is not None else 0.5
        t["art"] = norm_enum(t.get("art"), prompts.THEMA_ART) or "sonstiges"
        t["zeithorizont"] = norm_enum(t.get("zeithorizont"), prompts.HORIZONS, {"kurzfristig": "kurz", "mittelfristig": "mittel", "langfristig": "lang"}) or "mittel"
        t["richtung"] = norm_enum(t.get("richtung"), ["positiv", "negativ"], {"bullish": "positiv", "bearish": "negativ"}) or "positiv"
        themes.append(t)
    content["nicht_eingepreiste_themen"] = themes
    # Vorschlag.
    v, vdiags = _norm_vorschlag(content, held)
    diags.extend(vdiags)
    content["vorschlag"] = v
    return content, diags


def synthesize_company(
    item, events: list, insights: dict, factor_context: dict, hints: list[InterplayHint],
    market_reaction: dict, previous_synthesis: dict | None, settings, held: bool,
    scored: pd.DataFrame | None = None, model: str | None = None, period: str | None = None,
    calendar_event=None, koyfin_text: str | None = None, history_text: str | None = None,
) -> SynthesisResult:
    user = prompts.build_synthesis_user_prompt(
        item, factor_context, events, insights, hints, market_reaction, previous_synthesis, held,
        calendar_event, koyfin_text=koyfin_text, history_text=history_text,
    )
    parsed, raw, n = _call(user, prompts.SYNTHESIS_SCHEMA, prompts.SYNTHESIS_REQUIRED, settings, model)
    res = _result(KIND_SYNTHESIS, parsed, raw, n, settings, model, user, period)
    if parsed is None:
        return SynthesisResult(res, None, [])
    content, diags = _validate_synthesis(parsed, hints, held)
    res.content = content
    res.diagnostics = diags
    links = resolve_company_mentions(content.get("verbundene_unternehmen") or [], scored, item.uid)
    content["verbundene_unternehmen"] = [
        {**m, "uid_im_universum": l.get("related_uid")}
        for m, l in zip([x for x in content.get("verbundene_unternehmen") or [] if isinstance(x, dict)], links)
    ] if links else content.get("verbundene_unternehmen") or []
    v = content["vorschlag"]
    suggestion = {
        "uid": item.uid,
        "ticker": item.ticker,
        "suggestion_type": v["typ"],
        "confidence": v["konfidenz"],
        "rationale": v["begruendung"],
        "evidence": v["evidenz"],
        "quant_zone": getattr(item, "zone", None),
        "quant_score": getattr(item, "composite_score", None),
        "quant_consistency": v["quant_konsistenz"],
        "n_interplay": len(content["faktor_zusammenspiel"]),
        "n_unpriced": len(content["nicht_eingepreiste_themen"]),
    }
    for l in links:
        l["uid"] = item.uid
        l["ticker"] = item.ticker
    return SynthesisResult(res, suggestion, links)


# --------------------------------------------------------------------------- #
# Themen (Run-Ebene)
# --------------------------------------------------------------------------- #
def extract_company_themes(uid: str, ticker: str, sector, region, content: dict | None) -> list[dict]:
    out = []
    for t in (content or {}).get("nicht_eingepreiste_themen") or []:
        if not isinstance(t, dict) or not t.get("thema"):
            continue
        out.append(
            {
                "uid": uid, "ticker": ticker, "sector": sector, "region": region,
                "thema": t.get("thema"), "art": t.get("art"), "richtung": t.get("richtung"),
                "konfidenz": t.get("konfidenz"),
            }
        )
    return out


def cluster_themes(
    run_id: str, company_themes: list[dict], company_links: list[dict], settings,
    valid_uids: set[str], model: str | None = None,
) -> ThemesResult:
    contributors = {t["uid"] for t in company_themes}
    if len(contributors) < 2:
        return ThemesResult(None, [], [], 0)
    user = prompts.build_themes_user_prompt(run_id, company_themes, company_links)
    parsed, raw, n = _call(user, prompts.THEMES_SCHEMA, prompts.THEMES_REQUIRED, settings, model)
    res = _result(KIND_THEMES, parsed, raw, n, settings, model, user, run_id)
    if parsed is None:
        return ThemesResult(res, [], [], n)
    themes = []
    for t in parsed.get("themen") or []:
        if not isinstance(t, dict) or not t.get("titel"):
            continue
        uids = [str(u) for u in (t.get("betroffene_uids") or []) if str(u) in valid_uids]
        conf = norm_float(t.get("konfidenz"), 0.0, 1.0)
        conf = conf if conf is not None else 0.5
        themes.append(
            {
                "title": t.get("titel"),
                "description": t.get("beschreibung"),
                "kind": norm_enum(t.get("art"), ["sektor", "region", "makro", "firmenspezifisch"]) or "makro",
                "direction": norm_enum(t.get("richtung"), ["positiv", "negativ", "gemischt"], {"bullish": "positiv", "bearish": "negativ", "neutral": "gemischt"}) or "gemischt",
                "confidence": conf,
                "uids": uids,
                "sectors": [str(s) for s in t.get("sektoren") or []],
                "regions": [str(r) for r in t.get("regionen") or []],
                "evidence": [str(b) for b in t.get("belege") or []],
                "confirmation": t.get("bestaetigung_durch"),
            }
        )
    links = []
    for l in parsed.get("verknuepfungen") or []:
        if not isinstance(l, dict):
            continue
        a, b = str(l.get("uid_a") or ""), str(l.get("uid_b") or "")
        if a not in valid_uids or b not in valid_uids or a == b:
            continue
        relation = str(l.get("beziehung") or "sonstiges").lower()
        links.append(
            {
                "uid": a, "related_uid": b, "related_name": b,
                "relation": relation if relation in prompts.RELATIONS else "sonstiges",
                "context": str(l.get("beleg") or "")[:400], "source": "themes",
            }
        )
    return ThemesResult(res, themes, links, n)


# --------------------------------------------------------------------------- #
# Trend-Scan
# --------------------------------------------------------------------------- #
def _normalize_trend(t: dict) -> dict | None:
    if not isinstance(t, dict) or not t.get("titel"):
        return None
    conf = norm_float(t.get("konfidenz"), 0.0, 1.0)
    conf = conf if conf is not None else 0.5
    strength_f = norm_float(t.get("staerke"))
    strength = int(round(strength_f)) if strength_f is not None else 3
    return {
        "title": t.get("titel"),
        "description": t.get("beschreibung"),
        "kind": norm_enum(t.get("art"), prompts.TREND_ART) or "sonstiges",
        "horizon": norm_enum(t.get("zeithorizont"), prompts.HORIZONS, {"kurzfristig": "kurz", "mittelfristig": "mittel", "langfristig": "lang"}) or "mittel",
        "strength": max(1, min(5, strength)),
        "confidence": conf,
        "evidence": [e for e in t.get("evidenz") or [] if isinstance(e, dict)],
        "industry_groups": [str(g) for g in t.get("betroffene_industriegruppen") or []],
        "loser_note": t.get("verlierer_hinweis"),
    }


def scan_trends(
    run_id: str, news_items: list[dict], run_themes: list[dict], scored: pd.DataFrame,
    settings, held_uids: set[str], forced_uids: set[str] | None = None,
    model: str | None = None,
) -> TrendScanResult:
    diags: list[Diagnostic] = []
    if not news_items and not run_themes:
        diags.append(Diagnostic(SEV_INFO, "trend_scan_no_input", "Trend-Scan übersprungen: weder Markt-News noch Themen."))
        return TrendScanResult(None, [], [], 0, diags)

    news_text = trend_scan.compact_news_for_prompt(news_items)
    profile_text = trend_scan.render_group_profile(trend_scan.industry_group_profile(scored))
    # Ungedeckelte Kandidatenzahl entscheidet über ein- oder zweistufig.
    n_eligible = len(trend_scan.candidate_table(scored, settings, forced_uids=forced_uids, cap=10**9))
    mode = trend_scan.plan_trend_calls(n_eligible, settings)
    all_candidates = trend_scan.candidate_table(scored, settings, forced_uids=forced_uids)
    max_b = int(getattr(settings, "analyst_trend_max_beneficiaries", 8))
    max_t = int(getattr(settings, "analyst_trend_max_trends", 6))
    n_total = 0

    if mode == "single":
        user = prompts.build_trend_prompt_single(
            news_text, run_themes, profile_text, trend_scan.render_candidates(all_candidates), settings
        )
        parsed, raw, n = _call(user, prompts.TREND_SCHEMA, prompts.TREND_REQUIRED, settings, model)
        n_total += n
        candidates = all_candidates
    else:
        user_a = prompts.build_trend_prompt_a(news_text, run_themes, profile_text, settings)
        parsed_a, raw_a, n_a = _call(user_a, prompts.TREND_GROUPS_SCHEMA, prompts.TREND_REQUIRED, settings, model)
        n_total += n_a
        if parsed_a is None:
            res = _result(KIND_TREND, None, raw_a, n_total, settings, model, user_a, run_id)
            return TrendScanResult(res, [], [], n_total, diags)
        trends_a = [t for t in parsed_a.get("trends") or [] if isinstance(t, dict)][:max_t]
        groups = set()
        for t in trends_a:
            groups.update(str(g) for g in t.get("betroffene_industriegruppen") or [])
        groups.update(trend_scan.SECOND_ORDER_GROUPS)
        candidates = trend_scan.candidate_table(scored, settings, group_whitelist=sorted(groups), forced_uids=forced_uids)
        user = prompts.build_trend_prompt_b(trends_a, trend_scan.render_candidates(candidates), settings)
        parsed, raw, n = _call(user, prompts.TREND_SCHEMA, prompts.TREND_REQUIRED, settings, model)
        n_total += n

    res = _result(KIND_TREND, parsed, raw, n_total, settings, model, user, run_id)
    if parsed is None:
        return TrendScanResult(res, [], [], n_total, diags)
    cand_by_uid = {c["uid"]: c for c in candidates}
    trends: list[dict] = []
    links: list[dict] = []
    for t in (parsed.get("trends") or [])[:max_t]:
        norm = _normalize_trend(t)
        if norm is None:
            continue
        idx = len(trends)
        trends.append(norm)
        b_links, b_diags = trend_scan.validate_beneficiaries(t, cand_by_uid, scored, held_uids, max_b)
        diags.extend(b_diags)
        for l in b_links:
            l["trend_idx"] = idx
            links.append(l)
    if not trends:
        diags.append(Diagnostic(SEV_INFO, "trend_scan_empty", "Trend-Scan: keine Trends erkannt."))
    res.content = {"trends": trends, "links": links, "mode": mode, "n_candidates": len(candidates)}
    return TrendScanResult(res, trends, links, n_total, diags)
