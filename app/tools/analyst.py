"""CLI des KI-Analysten (Ollama Cloud + Alpha Vantage).

Beispiele::

    python -m app.tools.analyst run --limit 5
    python -m app.tools.analyst run --dry-run                 # keine LLM-Calls
    python -m app.tools.analyst run --scope custom --tickers SAP,MBG~mercedes
    python -m app.tools.analyst models                        # Ollama-Modelle
    python -m app.tools.analyst watchlist --limit 20
    python -m app.tools.analyst status | themes | trends [--run RUN_ID]
    python -m app.tools.analyst inspect --uid AAPL [--kind synthesis] [--raw]

Exit-Codes: ``0`` Erfolg/teilweise (Warnungen auf stderr), ``1`` fehlende
Voraussetzungen (Keys, Universum) oder Lauf-Fehler, ``2`` Argumentfehler —
geeignet für einen Scheduler-Lauf (cron, Docker).
"""

from __future__ import annotations

import argparse
import logging
import sys

from app.core.state import STATE


def _load_state():
    STATE.load_from_db()
    scored = STATE.scored
    if scored is None or scored.empty:
        print(
            "Kein Universum in der Datenbank — bitte zuerst auf /daten-import "
            "einen Koyfin-Export hochladen.",
            file=sys.stderr,
        )
        return None
    return scored


def _parse_tickers(raw: str | None) -> list[str]:
    if not raw:
        return []
    return [t.strip() for t in raw.replace("\n", ",").split(",") if t.strip()]


def _print_diags(result) -> None:
    from app.core.diagnostics import SEV_INFO, sort_diagnostics

    for d in sort_diagnostics(result.all_diagnostics()):
        if d.severity == SEV_INFO:
            continue
        who = f" [{d.uid}]" if d.uid else ""
        print(f"[{d.severity}] {d.code}{who}: {d.message}", file=sys.stderr)


def _cmd_run(args: argparse.Namespace) -> int:
    from app.core.analyst.runner import AnalystConfigError, run_analyst

    scored = _load_state()
    if scored is None:
        return 1
    tickers = _parse_tickers(args.tickers)
    scope = args.scope
    if tickers and not scope:
        scope = "custom"
    if scope == "custom" and not tickers and not STATE.settings.analyst_custom_tickers:
        print("Scope custom ohne --tickers und ohne gespeicherte Liste.", file=sys.stderr)
        return 2
    try:
        result = run_analyst(
            STATE.settings,
            scored,
            STATE,
            trigger="cli",
            scope=scope,
            limit=args.limit,
            tickers=tickers or None,
            dry_run=args.dry_run,
            model=args.model,
            out_dir=args.out,
            max_llm_calls=args.max_llm_calls,
            no_themes=args.no_themes,
            no_trends=args.no_trends,
            progress=lambda m: print(f"… {m}", file=sys.stderr) if args.verbose else None,
        )
    except AnalystConfigError as exc:
        print(f"Abbruch: {exc}", file=sys.stderr)
        return 1
    print(
        f"Lauf {result.run_id}: Status {result.status}"
        + (" (Trockenlauf)" if result.dry_run else "")
        + f" · {len(result.companies)} Titel · {result.n_events} Ereignisse · "
        f"{result.budget.llm_calls} LLM-Calls · {result.budget.av_calls} AV-Calls · "
        f"{sum(1 for c in result.companies if c.suggestion)} Vorschläge · "
        f"{len(result.themes)} Themen · {len(result.trends)} Trends"
    )
    if result.report_path:
        print(f"Report: {result.report_path}")
    _print_diags(result)
    if result.status == "error":
        print(f"Fehler: {result.error}", file=sys.stderr)
        return 1
    return 0


def _cmd_models(args: argparse.Namespace) -> int:
    from app.core.analyst import ollama_client

    if not ollama_client.api_key():
        print("OLLAMA_API_KEY ist nicht gesetzt.", file=sys.stderr)
        return 1
    models = ollama_client.list_models()
    if not models:
        print("Keine Modelle abrufbar (Netzwerk/Key prüfen).", file=sys.stderr)
        return 1
    for m in models:
        print(m)
    return 0


def _cmd_watchlist(args: argparse.Namespace) -> int:
    from app.core import av_client, market_data
    from app.core.analyst import store, watchlist

    scored = _load_state()
    if scored is None:
        return 1
    tickers = _parse_tickers(args.tickers)
    scope = args.scope or ("custom" if tickers else None)
    items, diags = watchlist.build_watchlist(STATE.settings, scored, STATE, scope=scope, tickers=tickers or None)
    watchlist.apply_watch_states(items, store.load_watch_states([i.uid for i in items]))
    items = watchlist.prioritize(items, args.limit)
    if av_client.api_key() and not args.no_resolve:
        diags += watchlist.resolve_av_symbols(items, scored, STATE.settings, market_data)
    print(f"{len(items)} Titel (Scope {scope or STATE.settings.analyst_scope}):")
    for it in items:
        checked = it.last_checked_at.strftime("%Y-%m-%d %H:%M") if it.last_checked_at else "nie"
        print(
            f"  {it.ticker:<8} {it.name[:32]:<32} {it.av_symbol or '–':<10} "
            f"Zone {it.zone or '–':<9} Score {it.composite_score if it.composite_score is not None else '–'} "
            f"{'Bestand ' if it.held else ''}[{', '.join(sorted(it.reasons))}] zuletzt {checked}"
        )
    for d in diags:
        print(f"[{d.severity}] {d.code}: {d.message}", file=sys.stderr)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from app.core.analyst import store

    runs = store.list_runs(limit=args.limit)
    if not runs:
        print("Noch keine Läufe.")
        return 0
    for r in runs:
        started = r["started_at"].strftime("%Y-%m-%d %H:%M") if r.get("started_at") else "–"
        print(
            f"{r['run_id']}  {r.get('status') or '–':<8} {r.get('trigger') or '–':<9} "
            f"{started}  Titel {r.get('n_companies') or 0}  Ereignisse {r.get('n_events') or 0}  "
            f"LLM {r.get('n_llm_calls') or 0}  AV {r.get('n_av_calls') or 0}  "
            f"Themen {r.get('n_themes') or 0}  Trends {r.get('n_trends') or 0}"
            + (" (dry)" if r.get("dry_run") else "")
            + (f"  Fehler: {r['error']}" if r.get("error") else "")
        )
    return 0


def _cmd_themes(args: argparse.Namespace) -> int:
    from app.core.analyst import store

    themes = store.list_themes(run_id=args.run)
    if not themes:
        print("Keine Themen gespeichert.")
        return 0
    for t in themes:
        print(f"- {t['title']} [{t.get('kind')}, {t.get('direction')}, Konfidenz {t.get('confidence')}]")
        if t.get("description"):
            print(f"  {t['description']}")
        print(f"  Titel: {', '.join(t.get('uids') or []) or '–'} · Bestätigung: {t.get('confirmation') or '–'}")
    return 0


def _cmd_trends(args: argparse.Namespace) -> int:
    from app.core.analyst import store

    trends = store.list_trends(run_id=args.run)
    if not trends:
        print("Keine Trends gespeichert.")
        return 0
    for t in trends:
        print(f"- {t['title']} [{t.get('kind')}, {t.get('horizon')}, Stärke {t.get('strength')}, Konfidenz {t.get('confidence')}]")
        if t.get("description"):
            print(f"  {t['description']}")
        for l in t.get("links") or []:
            print(
                f"    {l.get('ticker'):<8} {l.get('role'):<16} eingepreist {l.get('pricing_quant')} "
                f"(LLM: {l.get('pricing_llm') or '–'}) → {l.get('action')}"
            )
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    """Gespeichertes Insight eines Titels anzeigen (Fehlersuche)."""
    import json

    from app.core.analyst import store

    if args.uid:
        rec = store.load_insight(args.uid, args.kind)
    else:
        rows = store.list_insights(kind=args.kind, run_id=args.run, limit=1)
        rec = rows[0] if rows else None
    if rec is None:
        print(f"Kein Insight ({args.kind}) für {args.uid or 'Run-Ebene'} gefunden.", file=sys.stderr)
        return 1
    print(
        f"{rec.get('kind')} · uid {rec.get('uid') or '–'} · Lauf {rec.get('run_id')} · "
        f"Periode {rec.get('period')} · Status {rec.get('status')} · Modell {rec.get('model')} · "
        f"Calls {rec.get('n_calls')}"
    )
    if args.raw:
        print(rec.get("raw_text") or "(kein Rohtext)")
    else:
        content = rec.get("content")
        if content is None:
            print("(nicht parsebar — Rohtext mit --raw anzeigen)")
        else:
            print(json.dumps(content, ensure_ascii=False, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.tools.analyst", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="Analyst-Lauf ausführen")
    p_run.add_argument("--scope", choices=["portfolio", "universe", "custom"], help="Beobachtungsumfang (Default: Einstellungen)")
    p_run.add_argument("--tickers", help="Kommagetrennte uids/Ticker (impliziert --scope custom)")
    p_run.add_argument("--limit", type=int, help="Max. Titel je Lauf (Default: Einstellungen)")
    p_run.add_argument("--dry-run", action="store_true", help="Nur Daten/Ereignisse, keine LLM-Calls")
    p_run.add_argument("--model", help="Ollama-Modell (Default: Einstellungen)")
    p_run.add_argument("--out", help="Report-Verzeichnis (Default: Einstellungen)")
    p_run.add_argument("--max-llm-calls", type=int, dest="max_llm_calls", help="LLM-Budget je Lauf")
    p_run.add_argument("--no-themes", action="store_true", dest="no_themes", help="Themen-Clustering überspringen")
    p_run.add_argument("--no-trends", action="store_true", dest="no_trends", help="Trend-Scan überspringen")
    p_run.add_argument("--verbose", "-v", action="store_true", help="Fortschritt auf stderr")
    p_run.set_defaults(func=_cmd_run)

    p_models = sub.add_parser("models", help="Verfügbare Ollama-Cloud-Modelle")
    p_models.set_defaults(func=_cmd_models)

    p_wl = sub.add_parser("watchlist", help="Beobachtungsliste anzeigen (keine LLM-Calls)")
    p_wl.add_argument("--scope", choices=["portfolio", "universe", "custom"])
    p_wl.add_argument("--tickers")
    p_wl.add_argument("--limit", type=int)
    p_wl.add_argument("--no-resolve", action="store_true", dest="no_resolve", help="Keine AV-Symbolauflösung")
    p_wl.set_defaults(func=_cmd_watchlist)

    p_status = sub.add_parser("status", help="Letzte Läufe")
    p_status.add_argument("--limit", type=int, default=10)
    p_status.set_defaults(func=_cmd_status)

    p_themes = sub.add_parser("themes", help="Themen eines Laufs")
    p_themes.add_argument("--run", help="Run-ID (Default: letzter Lauf mit Themen)")
    p_themes.set_defaults(func=_cmd_themes)

    p_trends = sub.add_parser("trends", help="Trends & Profiteure eines Laufs")
    p_trends.add_argument("--run", help="Run-ID (Default: letzter Lauf mit Trends)")
    p_trends.set_defaults(func=_cmd_trends)

    p_inspect = sub.add_parser("inspect", help="Gespeichertes Insight (Synthese/Transkript/…) anzeigen")
    p_inspect.add_argument("--uid", help="uid des Titels (ohne: Run-Ebene, z. B. kind trend)")
    p_inspect.add_argument("--kind", default="synthesis", choices=["synthesis", "transcript_summary", "news_digest", "themes", "trend"])
    p_inspect.add_argument("--run", help="Run-ID (nur Run-Ebene)")
    p_inspect.add_argument("--raw", action="store_true", help="Rohantwort des Modells statt geparstem Inhalt")
    p_inspect.set_defaults(func=_cmd_inspect)

    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 2

    logging.basicConfig(level=logging.WARNING, format="[%(levelname)s] %(name)s: %(message)s")
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
