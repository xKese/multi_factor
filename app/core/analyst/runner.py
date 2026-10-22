"""Orchestrierung eines KI-Analyst-Laufs.

Ablauf (siehe Modul-Docstring des Pakets):

1. Guards (Universum, API-Keys) → :class:`AnalystConfigError`.
2. Beobachtungsliste → Priorisierung → AV-Symbole; ein Kalender-Call.
3. Je Titel: Ereignisse (Earnings, Transkript, Insider, News, Zone/Score),
   Kursreaktion, regelbasierte Faktor-Hinweise; Ereignisse speichern.
4. Je Titel mit neuem Material: Transkript → News-Digest → Synthese über
   alle Quellen; Insights, Vorschlag, Unternehmensverknüpfungen speichern;
   Cursor fortschreiben (nie im Trockenlauf).
5. Run-Ebene: Themen-Clustering, Trend-Scan (Markt-News je Topic).
6. Lauf-Zeile aktualisieren, Markdown-Report schreiben.

``av`` und ``ollama`` sind Modulattribute (Tests ersetzen sie), ebenso
``market_data`` für die Symbolauflösung.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

from .. import av_client, av_fundamentals, av_store, market_data
from ..agents_client import build_factor_context
from ..av_client import AlphaVantageError
from ..diagnostics import SEV_ERROR, SEV_INFO, SEV_WARNING, Diagnostic
from ..uid import base_ticker, row_by_uid
from . import events as ev
from . import ollama_client, report, snapshot, store, tasks, trend_scan, watchlist
from .factor_interplay import detect_interplay
from .ollama_client import OllamaError

log = logging.getLogger(__name__)

av = av_fundamentals
ollama = ollama_client

# Reserve an LLM-Calls für die Run-Ebene (Themen + Trend-Scan).
RUN_LEVEL_RESERVE = 3


class AnalystConfigError(RuntimeError):
    """Fehlende Voraussetzungen (Universum, API-Keys)."""


@dataclass
class RunBudget:
    max_companies: int
    max_llm_calls: int
    llm_calls: int = 0
    av_calls: int = 0
    av_blocked: bool = False
    llm_failed: bool = False
    exhausted: bool = False

    def llm_available(self, reserve: int = 0) -> bool:
        return not self.llm_failed and self.llm_calls < self.max_llm_calls - reserve


@dataclass
class CompanyResult:
    item: watchlist.WatchItem
    events: list = field(default_factory=list)
    insights: dict = field(default_factory=dict)
    hints: list = field(default_factory=list)
    market_reaction: dict = field(default_factory=dict)
    suggestion: dict | None = None
    links: list = field(default_factory=list)
    diagnostics: list = field(default_factory=list)
    llm_calls: int = 0


@dataclass
class RunResult:
    run_id: str
    trigger: str
    scope: str
    started_at: datetime
    finished_at: datetime | None
    status: str
    dry_run: bool
    model: str
    budget: RunBudget
    companies: list = field(default_factory=list)
    themes: list = field(default_factory=list)
    cross_links: list = field(default_factory=list)
    trends: list = field(default_factory=list)
    trend_links: list = field(default_factory=list)
    trend_inputs: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)
    report_path: str | None = None
    error: str | None = None

    @property
    def n_llm_calls(self) -> int:
        return self.budget.llm_calls

    @property
    def n_av_calls(self) -> int:
        return self.budget.av_calls

    @property
    def n_events(self) -> int:
        return sum(len(c.events) for c in self.companies)

    def all_diagnostics(self) -> list:
        out = list(self.diagnostics)
        for c in self.companies:
            out.extend(c.diagnostics)
        return out


def _snapshot_date(scored: pd.DataFrame, today: date) -> date:
    try:
        from ..signal_events import snapshot_date_from_universe

        return snapshot_date_from_universe(scored, None)
    except Exception:  # noqa: BLE001
        return today


def _noop(_msg: str) -> None:
    return None


# --------------------------------------------------------------------------- #
# Je Titel
# --------------------------------------------------------------------------- #
def _fetch_company(item, row, cursor, settings, budget, calendar_by_symbol, today, now, dry_run):
    """Alle Alpha-Vantage-Daten eines Titels → (events, extras, cursor_updates, diagnostics)."""
    rpm = int(getattr(settings, "risk_av_requests_per_minute", 70))
    events: list[ev.AnalystEvent] = []
    diags: list[Diagnostic] = []
    extras: dict = {"transcript": None, "news_items": [], "prices": None, "report_date": None}
    cursor_updates: dict = {}
    sym = item.av_symbol

    def _av(fn, *args, **kwargs):
        budget.av_calls += 1
        return fn(*args, **kwargs)

    if sym and not budget.av_blocked:
        try:
            earnings = _av(av.fetch_earnings, sym, requests_per_minute=rpm)
            e = ev.earnings_event(cursor, earnings, item.uid, item.ticker, today)
            if e is not None:
                events.append(e)
                cursor_updates["last_earnings_report_date"] = e.event_date
                extras["report_date"] = e.event_date
            latest = av.latest_reported_quarter(earnings)
            if latest and getattr(settings, "analyst_include_transcripts", True):
                quarter, reported = latest
                if ev.transcript_needed(
                    cursor, quarter, lambda q: store.has_insight(item.uid, tasks.KIND_TRANSCRIPT, q)
                ):
                    tr = _av(av.fetch_transcript, sym, quarter, requests_per_minute=rpm)
                    if tr:
                        extras["transcript"] = tr
                        events.append(ev.transcript_event(tr, item.uid, item.ticker, reported))
                        extras["report_date"] = extras["report_date"] or reported
                    else:
                        diags.append(Diagnostic(SEV_INFO, "transcript_unavailable",
                                                f"{item.ticker}: kein Earnings-Call-Transkript für {quarter} bei Alpha Vantage.", item.uid))
                        cursor_updates["last_transcript_quarter"] = quarter
            if getattr(settings, "analyst_include_insider", True):
                floor = (cursor or {}).get("last_insider_date") or (today - timedelta(days=ev.FIRST_RUN_INSIDER_DAYS))
                rows = _av(av.fetch_insider_transactions, sym, since=floor, requests_per_minute=rpm)
                e = ev.insider_event(cursor, rows, item.uid, item.ticker, today)
                if e is not None:
                    events.append(e)
                    cursor_updates["last_insider_date"] = e.event_date
            if getattr(settings, "analyst_include_news", True):
                since = (cursor or {}).get("last_news_at") or av.default_news_window(
                    now, int(getattr(settings, "analyst_news_window_days", 7))
                )
                feed = _av(
                    av.fetch_news_sentiment, tickers=[sym], time_from=since,
                    limit=int(getattr(settings, "analyst_news_limit", 20)), requests_per_minute=rpm,
                )
                min_rel = float(getattr(settings, "analyst_news_min_relevance", 0.3))
                e = ev.news_event(cursor, feed, item.uid, item.ticker, sym, min_rel)
                extras["news_items"] = ev.relevant_news(feed, sym, min_rel, None)
                if e is not None:
                    events.append(e)
                cursor_updates["last_news_at"] = now
            if getattr(settings, "analyst_include_price_reaction", True) and extras["report_date"]:
                try:
                    prices = _av(av_client.fetch_daily_adjusted, sym, "compact", rpm)
                    extras["prices"] = prices
                    if not dry_run:
                        try:
                            av_store.save_prices(sym, prices)
                        except Exception as exc:  # noqa: BLE001
                            log.info("Kurscache für %s nicht gespeichert: %s", sym, exc)
                except AlphaVantageError as exc:
                    if exc.kind == "rate_limit":
                        raise
                    diags.append(Diagnostic(SEV_INFO, "price_reaction_unavailable",
                                            f"{item.ticker}: Kursreaktion nicht abrufbar ({exc}).", item.uid))
        except AlphaVantageError as exc:
            if exc.kind == "rate_limit":
                budget.av_blocked = True
                diags.append(Diagnostic(SEV_WARNING, "av_rate_limit",
                                        f"Alpha-Vantage-Rate-Limit bei {item.ticker} — Datenabruf für die restlichen Titel übersprungen.", item.uid))
            else:
                diags.append(Diagnostic(SEV_WARNING, "av_error", f"{item.ticker}: Alpha-Vantage-Fehler — {exc}", item.uid))

    # Kalender (ein Call je Lauf, hier nur Lookup).
    cal_rows = []
    if sym:
        cal_rows = calendar_by_symbol.get(sym.upper(), [])
    if not cal_rows:
        cal_rows = calendar_by_symbol.get(base_ticker(item.uid).upper(), [])
    e = ev.upcoming_event(cal_rows, item.uid, item.ticker, today)
    if e is not None:
        events.append(e)

    return events, extras, cursor_updates, diags


def _analyse_company(item, row, cursor, settings, budget, events, extras, comp, run_id, model, scored, held, reserve=RUN_LEVEL_RESERVE):
    """LLM-Stufe eines Titels; schreibt Insights/Vorschlag/Links in die DB."""
    factor_context = build_factor_context(row, settings)
    insights: dict = {}
    cursor_updates: dict = {}
    transcript = extras.get("transcript")
    if transcript is not None and budget.llm_available(reserve):
        res = tasks.summarize_transcript(item, transcript, factor_context, comp.hints, settings, held, model)
        budget.llm_calls += res.n_calls
        comp.llm_calls += res.n_calls
        store.save_insight(_insight_record(res, run_id, item, res.period))
        insights[tasks.KIND_TRANSCRIPT] = {"content": res.content, "status": res.status, "period": res.period}
        if res.status == "ok":
            cursor_updates["last_transcript_quarter"] = res.period
        else:
            comp.diagnostics.append(Diagnostic(SEV_WARNING, "llm_unparsed",
                                               f"{item.ticker}: Transkript-Auswertung {res.period} nicht parsebar.", item.uid))
    news_event = next((e for e in events if e.kind == ev.KIND_NEWS), None)
    if news_event is not None and budget.llm_available(reserve):
        items = (news_event.payload or {}).get("items") or []
        res = tasks.digest_news(item, items, factor_context, settings, held, model, period=news_event.period)
        budget.llm_calls += res.n_calls
        comp.llm_calls += res.n_calls
        store.save_insight(_insight_record(res, run_id, item, res.period))
        insights[tasks.KIND_NEWS] = {"content": res.content, "status": res.status, "period": res.period}
        if res.status != "ok":
            comp.diagnostics.append(Diagnostic(SEV_WARNING, "llm_unparsed", f"{item.ticker}: News-Digest nicht parsebar.", item.uid))
    if budget.llm_available(reserve):
        previous = store.load_latest_synthesis(item.uid)
        try:
            koyfin_text = snapshot.render_snapshot_text(snapshot.koyfin_snapshot(row, settings))
            history_text = snapshot.render_history_text(snapshot.uid_history(item.uid))
        except Exception as exc:  # noqa: BLE001 — Zusatzquelle, nie blockierend
            log.info("Koyfin-Steckbrief für %s nicht erstellt: %s", item.ticker, exc)
            koyfin_text, history_text = None, None
        syn = tasks.synthesize_company(
            item, events, insights, factor_context, comp.hints, comp.market_reaction, previous,
            settings, held, scored=scored, model=model, period=run_id,
            koyfin_text=koyfin_text, history_text=history_text,
        )
        budget.llm_calls += syn.insight.n_calls
        comp.llm_calls += syn.insight.n_calls
        store.save_insight(_insight_record(syn.insight, run_id, item, run_id))
        insights[tasks.KIND_SYNTHESIS] = {"content": syn.insight.content, "status": syn.insight.status, "period": run_id}
        comp.diagnostics.extend(syn.insight.diagnostics)
        if syn.suggestion:
            store.save_suggestion({**syn.suggestion, "run_id": run_id})
            comp.suggestion = syn.suggestion
        if syn.links:
            store.save_company_links(run_id, syn.links)
            comp.links = syn.links
        if syn.insight.status != "ok":
            comp.diagnostics.append(Diagnostic(SEV_WARNING, "llm_unparsed", f"{item.ticker}: Synthese nicht parsebar.", item.uid))
    else:
        budget.exhausted = True
    comp.insights = insights
    return cursor_updates


def _insight_record(res: tasks.InsightResult, run_id: str, item, period) -> dict:
    return {
        "run_id": run_id,
        "uid": item.uid,
        "ticker": item.ticker,
        "kind": res.kind,
        "period": period,
        "status": res.status,
        "model": res.model,
        "content": res.content,
        "raw_text": res.raw_text,
        "prompt_hash": res.prompt_hash,
        "n_calls": res.n_calls,
    }


# --------------------------------------------------------------------------- #
# Lauf
# --------------------------------------------------------------------------- #
def run_analyst(
    settings,
    scored: pd.DataFrame | None,
    state=None,
    *,
    trigger: str = "cli",
    scope: str | None = None,
    limit: int | None = None,
    tickers: list[str] | None = None,
    dry_run: bool = False,
    model: str | None = None,
    out_dir: str | None = None,
    max_llm_calls: int | None = None,
    no_themes: bool = False,
    no_trends: bool = False,
    progress=None,
    now: datetime | None = None,
) -> RunResult:
    progress = progress or _noop
    now = now or datetime.now()
    today = now.date()

    if scored is None or scored.empty:
        raise AnalystConfigError("Kein Universum geladen — bitte zuerst einen Koyfin-Export importieren.")
    if not av_client.api_key():
        raise AnalystConfigError("ALPHAVANTAGE_API_KEY ist nicht gesetzt.")
    if not dry_run and not ollama.api_key():
        raise AnalystConfigError("OLLAMA_API_KEY ist nicht gesetzt.")

    run_id = now.strftime("%Y%m%d_%H%M%S")
    model_name = (model or getattr(settings, "analyst_model", "") or "gpt-oss:120b").strip()
    scope_name = (scope or getattr(settings, "analyst_scope", "portfolio") or "portfolio").lower()
    budget = RunBudget(
        max_companies=int(limit if limit is not None else getattr(settings, "analyst_max_companies_per_run", 25)),
        max_llm_calls=int(max_llm_calls if max_llm_calls is not None else getattr(settings, "analyst_max_llm_calls_per_run", 80)),
    )
    result = RunResult(
        run_id=run_id, trigger=trigger, scope=scope_name, started_at=now, finished_at=None,
        status="running", dry_run=dry_run, model=model_name, budget=budget,
    )
    store.save_run({
        "run_id": run_id, "trigger": trigger, "scope": scope_name, "model": model_name,
        "status": "running", "dry_run": dry_run, "started_at": now,
    })
    themes_enabled = bool(getattr(settings, "analyst_themes_enabled", True)) and not no_themes
    trends_enabled = bool(getattr(settings, "analyst_trends_enabled", True)) and not no_trends
    reserve = RUN_LEVEL_RESERVE if (themes_enabled or trends_enabled) else 0

    try:
        # 1. Beobachtungsliste
        progress("Beobachtungsliste aufbauen …")
        items, diags = watchlist.build_watchlist(settings, scored, state, scope=scope_name, tickers=tickers)
        result.diagnostics.extend(diags)
        states = store.load_watch_states([it.uid for it in items])
        watchlist.apply_watch_states(items, states)
        items = watchlist.prioritize(items, budget.max_companies)
        result.diagnostics.extend(watchlist.resolve_av_symbols(items, scored, settings, market_data))
        held = watchlist.held_uids(state)

        # 2. Kalender (ein Call)
        calendar_by_symbol: dict[str, list[dict]] = {}
        if items and any(it.av_symbol for it in items):
            try:
                budget.av_calls += 1
                rows = av.fetch_earnings_calendar(requests_per_minute=int(getattr(settings, "risk_av_requests_per_minute", 70)))
                for r in rows:
                    calendar_by_symbol.setdefault(str(r["symbol"]).upper(), []).append(r)
            except AlphaVantageError as exc:
                result.diagnostics.append(Diagnostic(SEV_WARNING, "calendar_unavailable", f"Earnings-Kalender nicht abrufbar: {exc}"))
                if exc.kind == "rate_limit":
                    budget.av_blocked = True

        # 3./4. Je Titel
        company_themes: list[dict] = []
        company_links: list[dict] = []
        snapshot_date = _snapshot_date(scored, today)
        for idx, item in enumerate(items, 1):
            progress(f"{item.ticker} ({idx}/{len(items)}) …")
            comp = CompanyResult(item=item)
            result.companies.append(comp)
            try:
                row = row_by_uid(scored, item.uid)
                if row is None:
                    comp.diagnostics.append(Diagnostic(SEV_WARNING, "uid_missing", f"{item.ticker}: nicht im Universum.", item.uid))
                    continue
                cursor = states.get(item.uid)
                events, extras, cursor_updates, fdiags = _fetch_company(
                    item, row, cursor, settings, budget, calendar_by_symbol, today, now, dry_run
                )
                comp.diagnostics.extend(fdiags)
                events.extend(ev.score_events(cursor, row, item.uid, item.ticker, snapshot_date))
                comp.events = events
                comp.hints = detect_interplay(row, cursor)
                comp.market_reaction = ev.build_market_reaction(row, extras.get("prices"), extras.get("news_items") or [], extras.get("report_date"))
                if not dry_run:
                    store.save_events(events, run_id)

                has_material = bool(events) or extras.get("transcript") is not None
                if has_material and not dry_run and not budget.llm_failed:
                    try:
                        llm_updates = _analyse_company(
                            item, row, cursor, settings, budget, events, extras, comp, run_id, model_name, scored, item.uid in held, reserve,
                        )
                        cursor_updates.update(llm_updates)
                    except OllamaError as exc:
                        budget.llm_failed = True
                        comp.diagnostics.append(Diagnostic(SEV_ERROR, "llm_error", f"Ollama-Fehler ({exc.kind}): {exc} — restliche LLM-Aufrufe übersprungen.", item.uid))
                    syn = (comp.insights.get(tasks.KIND_SYNTHESIS) or {}).get("content")
                    company_themes.extend(tasks.extract_company_themes(item.uid, item.ticker, row.get("sector"), row.get("region"), syn))
                    company_links.extend(comp.links)
                elif has_material and not dry_run and budget.llm_failed:
                    comp.diagnostics.append(Diagnostic(SEV_INFO, "llm_skipped", f"{item.ticker}: LLM-Stufe übersprungen (vorheriger Fehler).", item.uid))

                if not dry_run:
                    zone = row.get("zone_v2")
                    score = row.get("composite_score")
                    zq = row.get("z_quality")
                    store.save_watch_state(
                        item.uid,
                        av_symbol=item.av_symbol,
                        last_zone=None if zone is None or (isinstance(zone, float) and pd.isna(zone)) else str(zone),
                        last_score=None if score is None or pd.isna(score) else float(score),
                        last_z_quality=None if zq is None or pd.isna(zq) else float(zq),
                        last_checked_at=now,
                        **cursor_updates,
                    )
            except Exception as exc:  # noqa: BLE001 — ein Titel darf den Lauf nicht abbrechen
                log.exception("KI-Analyst: Titel %s fehlgeschlagen", item.ticker)
                comp.diagnostics.append(Diagnostic(SEV_ERROR, "company_failed", f"{item.ticker}: {exc.__class__.__name__}: {exc}", item.uid))

        # 5. Run-Ebene: Themen
        valid_uids = {str(u) for u in scored["uid"].astype(str)} if "uid" in scored.columns else set()
        if themes_enabled and not dry_run and not budget.llm_failed and budget.llm_available(0):
            progress("Themen clustern …")
            try:
                th = tasks.cluster_themes(run_id, company_themes, company_links, settings, valid_uids, model_name)
                budget.llm_calls += th.n_calls
                if th.insight is not None:
                    store.save_insight({**_run_insight(th.insight, run_id)})
                    if th.insight.status != "ok":
                        result.diagnostics.append(Diagnostic(SEV_WARNING, "themes_failed", "Themen-Clustering nicht parsebar."))
                if th.themes:
                    store.save_themes(run_id, th.themes)
                    result.themes = th.themes
                if th.links:
                    store.save_company_links(run_id, th.links)
                    result.cross_links = th.links
            except OllamaError as exc:
                budget.llm_failed = True
                result.diagnostics.append(Diagnostic(SEV_ERROR, "llm_error", f"Ollama-Fehler beim Themen-Clustering ({exc.kind}): {exc}"))

        # 5. Run-Ebene: Trend-Scan
        if trends_enabled:
            progress("Trend-Scan …")
            news_items: list[dict] = []
            if not budget.av_blocked:
                av_counter: list = []
                window = av.default_news_window(now, int(getattr(settings, "analyst_news_window_days", 7)))
                news_items, ndiags = trend_scan.collect_market_news(settings, window, av, av_counter)
                budget.av_calls += len(av_counter)
                result.diagnostics.extend(ndiags)
                if any(d.code == "trend_news_failed" and "rate_limit" in d.message for d in ndiags):
                    budget.av_blocked = True
            else:
                result.diagnostics.append(Diagnostic(SEV_INFO, "trend_scan_no_news", "Trend-Scan ohne Markt-News (AV-Rate-Limit) — nur Themen."))
            forced = {it.uid for it in items} | {t["uid"] for t in company_themes}
            if dry_run:
                cands = trend_scan.candidate_table(scored, settings, forced_uids=forced)
                n_eligible = len(trend_scan.candidate_table(scored, settings, forced_uids=forced, cap=10**9))
                per_group: dict[str, int] = {}
                for c in cands:
                    per_group[c["industry_group"]] = per_group.get(c["industry_group"], 0) + 1
                per_topic: dict[str, int] = {}
                for n in news_items:
                    per_topic[n.get("query_topic", "?")] = per_topic.get(n.get("query_topic", "?"), 0) + 1
                result.trend_inputs = {
                    "n_news": len(news_items), "news_per_topic": per_topic, "n_candidates": len(cands),
                    "candidates_per_group": per_group, "mode": trend_scan.plan_trend_calls(n_eligible, settings),
                }
            elif not budget.llm_failed and budget.llm_available(0):
                try:
                    ts = tasks.scan_trends(run_id, news_items, result.themes, scored, settings, held, forced, model_name)
                    budget.llm_calls += ts.n_calls
                    result.diagnostics.extend(ts.diagnostics)
                    if ts.insight is not None:
                        store.save_insight({**_run_insight(ts.insight, run_id)})
                        if ts.insight.status != "ok":
                            result.diagnostics.append(Diagnostic(SEV_WARNING, "trends_failed", "Trend-Scan nicht parsebar."))
                    if ts.trends:
                        store.save_trends(run_id, ts.trends, ts.links)
                        result.trends = ts.trends
                        result.trend_links = ts.links
                except OllamaError as exc:
                    budget.llm_failed = True
                    result.diagnostics.append(Diagnostic(SEV_ERROR, "llm_error", f"Ollama-Fehler beim Trend-Scan ({exc.kind}): {exc}"))
            else:
                result.diagnostics.append(Diagnostic(SEV_INFO, "trend_scan_skipped", "Trend-Scan übersprungen (LLM-Budget oder -Fehler)."))

        if budget.exhausted:
            result.diagnostics.append(Diagnostic(SEV_INFO, "llm_budget_exhausted",
                                                 f"LLM-Budget ({budget.max_llm_calls} Calls) erschöpft — nicht alle Titel ausgewertet."))
        result.status = "partial" if (budget.av_blocked or budget.llm_failed or budget.exhausted) else "done"
    except Exception as exc:  # noqa: BLE001
        log.exception("KI-Analyst-Lauf %s abgebrochen", run_id)
        result.status = "error"
        result.error = f"{exc.__class__.__name__}: {exc}"
        result.diagnostics.append(Diagnostic(SEV_ERROR, "run_failed", result.error))

    # 6. Abschluss
    result.finished_at = datetime.now()
    try:
        result.report_path = str(report.write_report(result, out_dir or getattr(settings, "analyst_report_dir", "reports")))
    except Exception as exc:  # noqa: BLE001
        result.diagnostics.append(Diagnostic(SEV_WARNING, "report_failed", f"Report nicht geschrieben: {exc}"))
    try:
        store.update_run(
            run_id,
            status=result.status, finished_at=result.finished_at,
            n_companies=len(result.companies), n_events=result.n_events,
            n_llm_calls=budget.llm_calls, n_av_calls=budget.av_calls,
            n_themes=len(result.themes), n_trends=len(result.trends),
            report_path=result.report_path, diagnostics=[d.__dict__ for d in result.all_diagnostics()],
            error=result.error,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("Lauf-Zeile nicht aktualisiert: %s", exc)
    return result


def _run_insight(res: tasks.InsightResult, run_id: str) -> dict:
    return {
        "run_id": run_id, "uid": None, "ticker": None, "kind": res.kind, "period": run_id,
        "status": res.status, "model": res.model, "content": res.content, "raw_text": res.raw_text,
        "prompt_hash": res.prompt_hash, "n_calls": res.n_calls,
    }
