"""Markdown-Report eines KI-Analyst-Laufs (``reports/ki_analyst_<run_id>.md``).

Dash-frei; Dezimalkomma; Reihenfolge: Kopf → Trends & Profiteure → Themen →
Vorschläge → je Titel (Call, News, Insider, Faktor-Zusammenspiel,
Quellen-Zusammenhänge, nicht eingepreiste Themen, verbundene Unternehmen)
→ firmenübergreifende Verknüpfungen → Ereignisse → Diagnose.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..diagnostics import sort_diagnostics
from .prompts import DISCLAIMER


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return "_keine Einträge_"
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _cell(value) -> str:
    text = "–" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def _num(value, digits: int = 1) -> str:
    if value is None:
        return "–"
    try:
        return f"{float(value):.{digits}f}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _pct(value, digits: int = 1) -> str:
    if value is None:
        return "–"
    try:
        return f"{float(value) * 100:.{digits}f} %".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def _dt(value) -> str:
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    return "–" if value is None else str(value)


def _join(values, sep: str = "; ") -> str:
    if not values:
        return "–"
    return sep.join(str(v) for v in values if v)


def _section_head(result) -> str:
    budget = result.budget
    lines = [
        f"# KI-Analyst — Lauf {result.run_id}" + (" (Trockenlauf)" if result.dry_run else ""),
        "",
        f"**Auslöser:** {result.trigger} · **Scope:** {result.scope} · **Modell:** {result.model} · "
        f"**Status:** {result.status} · **Start:** {_dt(result.started_at)} · **Ende:** {_dt(result.finished_at)}",
        "",
        f"**Budget:** {len(result.companies)} Titel geprüft (max. {budget.max_companies}), "
        f"{budget.llm_calls} LLM-Calls (max. {budget.max_llm_calls}), {budget.av_calls} Alpha-Vantage-Calls"
        + (" · AV-Rate-Limit erreicht" if budget.av_blocked else ""),
        "",
        f"> {DISCLAIMER}",
        "",
    ]
    return "\n".join(lines)


def _section_trends(result) -> str:
    out = ["## Trends & Profiteure", ""]
    if not result.trends:
        out.append("_Keine Trends in diesem Lauf._")
        if result.dry_run and result.trend_inputs:
            out.append("")
            out.append(_trend_inputs(result))
        return "\n".join(out)
    links_by_idx: dict[int, list[dict]] = {}
    for l in result.trend_links:
        links_by_idx.setdefault(int(l.get("trend_idx", -1)), []).append(l)
    for idx, t in enumerate(result.trends):
        out.append(f"### {idx + 1}. {t.get('title')}")
        out.append("")
        out.append(
            f"{t.get('description') or ''}  \n"
            f"**Art:** {t.get('kind')} · **Horizont:** {t.get('horizon')} · **Stärke:** {t.get('strength')}/5 · "
            f"**Konfidenz:** {_num(t.get('confidence'), 2)} · **Industriegruppen:** {_join(t.get('industry_groups'), ', ')}"
        )
        ev = [f"{e.get('quelle')}: {e.get('referenz')}" for e in t.get("evidence") or [] if isinstance(e, dict)]
        out.append(f"Evidenz: {_join(ev)}")
        if t.get("loser_note"):
            out.append(f"Verlierer-Hinweis: {t['loser_note']}")
        out.append("")
        rows = [
            [
                l.get("ticker"), l.get("name"), l.get("role"),
                f"{l.get('pricing_llm') or '–'} / {l.get('pricing_quant')}",
                l.get("action"), _join(l.get("reasoning"), " → "),
                f"{l.get('zone') or '–'} / {_num(l.get('composite_score'))}",
            ]
            for l in links_by_idx.get(idx, [])
        ]
        out.append(_table(["Ticker", "Name", "Rolle", "Eingepreist (LLM / Quant)", "Aktion", "Begründungskette", "Zone / Score"], rows))
        out.append("")
    if result.dry_run and result.trend_inputs:
        out.append(_trend_inputs(result))
    return "\n".join(out)


def _trend_inputs(result) -> str:
    ti = result.trend_inputs or {}
    lines = ["### Trend-Scan-Eingaben (Trockenlauf)", ""]
    lines.append(f"Markt-News im Fenster: {ti.get('n_news', 0)}")
    per_topic = ti.get("news_per_topic") or {}
    if per_topic:
        lines.append(_table(["Topic", "News"], [[k, str(v)] for k, v in sorted(per_topic.items())]))
    per_group = ti.get("candidates_per_group") or {}
    lines.append("")
    lines.append(f"Kandidaten gesamt: {ti.get('n_candidates', 0)} (Modus: {ti.get('mode', '–')})")
    if per_group:
        lines.append(_table(["Industriegruppe", "Kandidaten"], [[k, str(v)] for k, v in sorted(per_group.items())]))
    return "\n".join(lines)


def _section_themes(result) -> str:
    out = ["## Themen (Universum)", ""]
    rows = [
        [
            t.get("title"), t.get("kind"), t.get("direction"), _num(t.get("confidence"), 2),
            _join(t.get("uids"), ", "), t.get("confirmation"),
        ]
        for t in result.themes
    ]
    out.append(_table(["Thema", "Art", "Richtung", "Konfidenz", "Betroffene Titel", "Bestätigung durch"], rows))
    for t in result.themes:
        if t.get("description"):
            out.append("")
            out.append(f"- **{t.get('title')}:** {t['description']}")
    return "\n".join(out)


def _section_suggestions(result) -> str:
    out = ["## Vorschläge", ""]
    rows = []
    for c in result.companies:
        s = c.suggestion
        if not s:
            continue
        rows.append(
            [
                c.item.ticker, c.item.name, s.get("suggestion_type"), _num(s.get("confidence"), 2),
                f"{s.get('quant_zone') or '–'} / {_num(s.get('quant_score'))}", s.get("quant_consistency"),
                str(s.get("n_interplay", 0)), str(s.get("n_unpriced", 0)), s.get("rationale"),
            ]
        )
    out.append(_table(
        ["Ticker", "Name", "Vorschlag", "Konfidenz", "Zone / Score", "Quant-Konsistenz", "Zusammenspiel", "Themen", "Begründung"],
        rows,
    ))
    return "\n".join(out)


def _company_section(c) -> str:
    out = [f"### {c.item.ticker} — {c.item.name}", ""]
    out.append(
        f"Zone {c.item.zone or '–'} · Composite {_num(c.item.composite_score)} · "
        f"{'im Bestand' if c.item.held else 'nicht im Bestand'} · AV-Symbol {c.item.av_symbol or '–'} · "
        f"Gründe: {_join(sorted(c.item.reasons), ', ')}"
    )
    if c.market_reaction:
        mr = c.market_reaction
        parts = []
        if "ret_since_report" in mr:
            parts.append(f"Kurs seit Bericht {_pct(mr['ret_since_report'])} ({mr.get('days_since_report')} Tage)")
        for k, label in (("ret_1m", "1M"), ("ret_3m", "3M"), ("mom_12_1", "12-1")):
            if k in mr:
                parts.append(f"{label} {_pct(mr[k])}")
        if "news_sentiment_recent" in mr:
            parts.append(
                f"News-Sentiment neu {_num(mr['news_sentiment_recent'], 2)} / früher {_num(mr.get('news_sentiment_earlier'), 2)}"
            )
        if parts:
            out.append(f"Markt-Reaktion: {' · '.join(parts)}")
    out.append("")

    tr = (c.insights.get("transcript_summary") or {}).get("content") if c.insights else None
    if tr:
        g = tr.get("guidance") or {}
        out.append(f"**Earnings-Call {c.insights['transcript_summary'].get('period') or ''}:** {tr.get('zusammenfassung')}")
        out.append(
            f"Guidance: {g.get('aenderung')} ({g.get('details')}) · Ton {tr.get('ton')}/5 · "
            f"Vertrauen {tr.get('vertrauen_management')}/5 · Quant-Abgleich: {(tr.get('quant_abgleich') or {}).get('urteil')} — "
            f"{(tr.get('quant_abgleich') or {}).get('begruendung')}"
        )
        for label, key in (("Kernaussagen", "kernaussagen"), ("Risiken", "risiken"), ("Q&A", "qa_highlights"), ("Glaubwürdigkeit", "glaubwuerdigkeit_flags")):
            vals = tr.get(key) or []
            if vals:
                out.append(f"- {label}: {_join(vals)}")
        out.append("")
    elif c.insights.get("transcript_summary"):
        out.append(f"**Earnings-Call:** Auswertung nicht parsebar (Status {c.insights['transcript_summary'].get('status')}).")
        out.append("")

    nd = (c.insights.get("news_digest") or {}).get("content") if c.insights else None
    if nd:
        out.append(f"**News-Digest:** {nd.get('zusammenfassung')} (Wesentlichkeit {nd.get('wesentlichkeit')}/5)")
        for t in nd.get("themen") or []:
            if isinstance(t, dict):
                out.append(f"- {t.get('titel')} [{t.get('bewertung')}] Quellen {_join(t.get('quellen'), ', ')}")
        out.append("")

    insider = next((e for e in c.events if e.kind == "insider"), None)
    if insider is not None:
        out.append(f"**Insider:** {insider.title}")
        out.append("")

    syn = (c.insights.get("synthesis") or {}).get("content") if c.insights else None
    if syn:
        out.append("**Faktor-Zusammenspiel**")
        out.append("")
        out.append(_table(
            ["Muster", "Faktoren", "Richtung", "Evidenz", "Interpretation", "Regel"],
            [
                [x.get("muster"), _join(x.get("faktoren"), ", "), x.get("richtung"), x.get("evidenz_urteil"),
                 x.get("interpretation"), x.get("regel_code") or "–"]
                for x in syn.get("faktor_zusammenspiel") or []
            ],
        ))
        out.append("")
        out.append("**Quellenübergreifende Zusammenhänge**")
        out.append("")
        out.append(_table(
            ["Quellen", "Befund", "Richtung", "Bedeutung", "Belege"],
            [
                [_join(z.get("quellen"), " ↔ "), z.get("befund"), z.get("richtung"), str(z.get("bedeutung")), _join(z.get("belege"))]
                for z in syn.get("quellen_zusammenhaenge") or []
            ],
        ))
        out.append("")
        out.append("**Nicht eingepreiste Themen**")
        out.append("")
        out.append(_table(
            ["Thema", "Art", "Richtung", "Horizont", "Konfidenz", "Warum nicht eingepreist", "Bestätigung durch"],
            [
                [t.get("thema"), t.get("art"), t.get("richtung"), t.get("zeithorizont"), _num(t.get("konfidenz"), 2),
                 t.get("warum_nicht_eingepreist"), t.get("bestaetigung_durch")]
                for t in syn.get("nicht_eingepreiste_themen") or []
            ],
        ))
        links = c.links or []
        if links:
            out.append("")
            out.append("**Verbundene Unternehmen**")
            out.append("")
            out.append(_table(
                ["Unternehmen", "Im Universum", "Beziehung", "Kontext", "Quelle"],
                [[l.get("related_name"), l.get("related_uid") or "–", l.get("relation"), l.get("context"), l.get("source")] for l in links],
            ))
        out.append("")
    elif c.insights.get("synthesis"):
        out.append(f"**Synthese:** nicht parsebar (Status {c.insights['synthesis'].get('status')}).")
        out.append("")
    if c.hints and not syn:
        out.append("**Regelbasierte Hinweise:** " + _join(f"{h.label} [{h.code}]" for h in c.hints))
        out.append("")
    return "\n".join(out)


def _section_companies(result) -> str:
    out = ["## Titel", ""]
    analysed = [c for c in result.companies if c.insights or c.hints or c.events]
    if not analysed:
        out.append("_Keine Titel mit neuem Material._")
        return "\n".join(out)
    for c in analysed:
        out.append(_company_section(c))
    return "\n".join(out)


def _section_links(result) -> str:
    out = ["## Firmenübergreifende Verknüpfungen", ""]
    rows = [
        [l.get("uid"), l.get("related_name") or l.get("related_uid"), l.get("relation"), l.get("context")]
        for l in result.cross_links
    ]
    out.append(_table(["Titel", "Verbunden mit", "Beziehung", "Beleg"], rows))
    return "\n".join(out)


def _section_events(result) -> str:
    out = ["## Ereignisse", ""]
    rows = []
    for c in result.companies:
        for e in c.events:
            rows.append([c.item.ticker, e.kind, e.event_date.isoformat() if e.event_date else "–", e.title])
    out.append(_table(["Ticker", "Art", "Datum", "Ereignis"], rows))
    return "\n".join(out)


def _section_diagnostics(result) -> str:
    out = ["## Diagnose", ""]
    diags = list(result.diagnostics)
    for c in result.companies:
        diags.extend(c.diagnostics)
    rows = [[d.severity, d.code, d.uid or "–", d.message] for d in sort_diagnostics(diags)]
    out.append(_table(["Schwere", "Code", "Titel", "Meldung"], rows))
    return "\n".join(out)


def build_markdown_report(result) -> str:
    parts = [
        _section_head(result),
        _section_trends(result),
        _section_themes(result),
        _section_suggestions(result),
        _section_companies(result),
        _section_links(result),
        _section_events(result),
        _section_diagnostics(result),
    ]
    return "\n\n".join(parts) + "\n"


def write_report(result, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    suffix = "_dry" if result.dry_run else ""
    path = out / f"ki_analyst_{result.run_id}{suffix}.md"
    path.write_text(build_markdown_report(result), encoding="utf-8")
    return path
