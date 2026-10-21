"""Ereignis-Erkennung des KI-Analysten (rein, ohne HTTP).

Jeder Detektor vergleicht frisch geholte Daten mit dem Cursor eines Titels
(Zeile ``analyst_watch_state``) und liefert :class:`AnalystEvent`-Objekte.
``dedupe_key`` (``uid|kind|period``) ist der UNIQUE-Schlüssel in der DB —
ein Ereignis wird über Läufe hinweg nur einmal gespeichert.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

KIND_EARNINGS = "earnings"
KIND_UPCOMING = "earnings_upcoming"
KIND_TRANSCRIPT = "transcript"
KIND_INSIDER = "insider"
KIND_NEWS = "news"
KIND_ZONE = "zone"
KIND_SCORE = "score"

KIND_LABELS = {
    KIND_EARNINGS: "Quartalszahlen",
    KIND_UPCOMING: "Berichtstermin",
    KIND_TRANSCRIPT: "Earnings-Call",
    KIND_INSIDER: "Insider-Transaktionen",
    KIND_NEWS: "News",
    KIND_ZONE: "Zonenwechsel",
    KIND_SCORE: "Score-Sprung",
}

FIRST_RUN_EARNINGS_DAYS = 120
FIRST_RUN_INSIDER_DAYS = 90
UPCOMING_WINDOW_DAYS = 21
SCORE_JUMP_THRESHOLD = 10.0


@dataclass
class AnalystEvent:
    kind: str
    uid: str
    ticker: str
    period: str
    event_date: date
    title: str
    payload: dict = field(default_factory=dict)
    dedupe_key: str = ""

    def __post_init__(self) -> None:
        if not self.dedupe_key:
            self.dedupe_key = f"{self.uid}|{self.kind}|{self.period}"


def _fmt_num(value, digits: int = 2) -> str:
    if value is None:
        return "–"
    return f"{float(value):.{digits}f}".replace(".", ",")


# --------------------------------------------------------------------------- #
# Detektoren
# --------------------------------------------------------------------------- #
def earnings_event(
    cursor: dict | None, earnings: list[dict], uid: str, ticker: str, today: date
) -> AnalystEvent | None:
    """Neuester Quartalsbericht, der nach dem Cursor liegt (Erstlauf:
    innerhalb von ``FIRST_RUN_EARNINGS_DAYS``)."""
    last = (cursor or {}).get("last_earnings_report_date")
    floor = last if last else today - timedelta(days=FIRST_RUN_EARNINGS_DAYS)
    candidates = [
        r for r in earnings
        if r.get("reported_date") and r["reported_date"] > floor and r["reported_date"] <= today
    ]
    if not candidates:
        return None
    newest = max(candidates, key=lambda r: r["reported_date"])
    surprise = newest.get("surprise_pct")
    direction = (
        "über" if surprise is not None and surprise > 0
        else "unter" if surprise is not None and surprise < 0 else "auf Höhe der"
    )
    title = (
        f"Quartalszahlen {newest['reported_date'].isoformat()}: EPS "
        f"{_fmt_num(newest.get('reported_eps'))} vs. Schätzung "
        f"{_fmt_num(newest.get('estimated_eps'))} ({direction} Erwartung"
        + (f", {_fmt_num(surprise, 1)} %" if surprise is not None else "")
        + ")"
    )
    return AnalystEvent(
        kind=KIND_EARNINGS,
        uid=uid,
        ticker=ticker,
        period=newest["reported_date"].isoformat(),
        event_date=newest["reported_date"],
        title=title,
        payload={
            "fiscal_date_ending": newest["fiscal_date_ending"].isoformat()
            if newest.get("fiscal_date_ending") else None,
            "reported_eps": newest.get("reported_eps"),
            "estimated_eps": newest.get("estimated_eps"),
            "surprise": newest.get("surprise"),
            "surprise_pct": surprise,
        },
    )


def upcoming_event(
    calendar_rows: list[dict], uid: str, ticker: str, today: date
) -> AnalystEvent | None:
    """Nächster Berichtstermin innerhalb von ``UPCOMING_WINDOW_DAYS``."""
    future = [
        r for r in calendar_rows
        if r.get("report_date") and today <= r["report_date"] <= today + timedelta(days=UPCOMING_WINDOW_DAYS)
    ]
    if not future:
        return None
    nxt = min(future, key=lambda r: r["report_date"])
    days = (nxt["report_date"] - today).days
    return AnalystEvent(
        kind=KIND_UPCOMING,
        uid=uid,
        ticker=ticker,
        period=nxt["report_date"].isoformat(),
        event_date=nxt["report_date"],
        title=f"Berichtstermin in {days} Tagen ({nxt['report_date'].isoformat()})"
        + (f", EPS-Schätzung {_fmt_num(nxt.get('estimate'))}" if nxt.get("estimate") is not None else ""),
        payload={"estimate": nxt.get("estimate"), "fiscal_date_ending": (
            nxt["fiscal_date_ending"].isoformat() if nxt.get("fiscal_date_ending") else None
        )},
    )


def transcript_needed(cursor: dict | None, quarter: str | None, has_insight) -> bool:
    """Neues Transkript-Quartal, das noch nicht ausgewertet wurde.

    ``has_insight(quarter) -> bool`` prüft die Insight-Tabelle (Idempotenz
    über Läufe hinweg, auch wenn der Cursor fehlt)."""
    if not quarter:
        return False
    if (cursor or {}).get("last_transcript_quarter") == quarter:
        return False
    return not has_insight(quarter)


def transcript_event(
    transcript: dict, uid: str, ticker: str, event_date: date
) -> AnalystEvent:
    segments = transcript.get("segments") or []
    n_chars = sum(len(s.get("content", "")) for s in segments)
    return AnalystEvent(
        kind=KIND_TRANSCRIPT,
        uid=uid,
        ticker=ticker,
        period=str(transcript.get("quarter")),
        event_date=event_date,
        title=f"Earnings-Call {transcript.get('quarter')} verfügbar "
        f"({len(segments)} Segmente, {n_chars // 1000} Tsd. Zeichen)",
        payload={"n_segments": len(segments), "n_chars": n_chars},
    )


def insider_event(
    cursor: dict | None, rows: list[dict], uid: str, ticker: str, today: date
) -> AnalystEvent | None:
    """Ein aggregiertes Ereignis je Lauf für alle neuen Transaktionen."""
    last = (cursor or {}).get("last_insider_date")
    floor = last if last else today - timedelta(days=FIRST_RUN_INSIDER_DAYS)
    new = [r for r in rows if r.get("transaction_date") and r["transaction_date"] > floor]
    if not new:
        return None
    buys = [r for r in new if r.get("acquisition_or_disposal") == "A"]
    sells = [r for r in new if r.get("acquisition_or_disposal") == "D"]
    net = sum(r.get("shares") or 0 for r in buys) - sum(r.get("shares") or 0 for r in sells)
    newest = max(r["transaction_date"] for r in new)
    return AnalystEvent(
        kind=KIND_INSIDER,
        uid=uid,
        ticker=ticker,
        period=newest.isoformat(),
        event_date=newest,
        title=f"Insider: {len(buys)} Käufe, {len(sells)} Verkäufe "
        f"(netto {_fmt_num(net, 0)} Aktien) seit {floor.isoformat()}",
        payload={
            "n_buys": len(buys),
            "n_sells": len(sells),
            "net_shares": net,
            "transactions": [
                {
                    "date": r["transaction_date"].isoformat(),
                    "executive": r.get("executive"),
                    "title": r.get("executive_title"),
                    "type": r.get("acquisition_or_disposal"),
                    "shares": r.get("shares"),
                    "price": r.get("share_price"),
                }
                for r in sorted(new, key=lambda r: r["transaction_date"], reverse=True)[:25]
            ],
        },
    )


def relevant_news(
    feed: list[dict], av_symbol: str | None, min_relevance: float, since: datetime | None
) -> list[dict]:
    """Items, in denen der Titel relevant ist und die nach ``since`` liegen."""
    out = []
    sym = (av_symbol or "").upper()
    for item in feed:
        ts = item.get("time_published")
        if since is not None and (ts is None or ts <= since):
            continue
        rel = None
        sent = None
        for t in item.get("ticker_sentiment") or []:
            if str(t.get("ticker", "")).upper() == sym:
                rel = t.get("relevance_score")
                sent = t.get("sentiment_score")
                break
        if sym and (rel is None or rel < min_relevance):
            continue
        out.append({**item, "relevance": rel, "ticker_sentiment_score": sent})
    out.sort(key=lambda i: i.get("time_published") or datetime.min, reverse=True)
    return out


def news_event(
    cursor: dict | None,
    feed: list[dict],
    uid: str,
    ticker: str,
    av_symbol: str | None,
    min_relevance: float,
) -> AnalystEvent | None:
    since = (cursor or {}).get("last_news_at")
    items = relevant_news(feed, av_symbol, min_relevance, since)
    if not items:
        return None
    newest = items[0]["time_published"]
    scores = [i["ticker_sentiment_score"] for i in items if i.get("ticker_sentiment_score") is not None]
    mean_sent = sum(scores) / len(scores) if scores else None
    return AnalystEvent(
        kind=KIND_NEWS,
        uid=uid,
        ticker=ticker,
        period=newest.isoformat(timespec="minutes"),
        event_date=newest.date(),
        title=f"{len(items)} neue News"
        + (f", Ø Sentiment {_fmt_num(mean_sent)}" if mean_sent is not None else ""),
        payload={
            "n_items": len(items),
            "mean_sentiment": mean_sent,
            "items": [
                {
                    "title": i.get("title"),
                    "url": i.get("url"),
                    "source": i.get("source"),
                    "time": i["time_published"].isoformat(timespec="minutes"),
                    "summary": (i.get("summary") or "")[:400],
                    "sentiment": i.get("ticker_sentiment_score"),
                    "relevance": i.get("relevance"),
                }
                for i in items[:25]
            ],
        },
    )


def score_events(
    cursor: dict | None, row, uid: str, ticker: str, snapshot_date: date
) -> list[AnalystEvent]:
    """Zonenwechsel und Score-Sprünge gegenüber dem Cursor (Erstlauf: keine)."""
    if not cursor:
        return []
    out: list[AnalystEvent] = []
    zone = row.get("zone_v2")
    zone = None if zone is None or (isinstance(zone, float) and pd.isna(zone)) else str(zone)
    last_zone = cursor.get("last_zone")
    if last_zone and zone and zone != last_zone:
        out.append(
            AnalystEvent(
                kind=KIND_ZONE,
                uid=uid,
                ticker=ticker,
                period=f"{snapshot_date.isoformat()}|{last_zone}->{zone}",
                event_date=snapshot_date,
                title=f"Zone {last_zone} → {zone}",
                payload={"from": last_zone, "to": zone},
            )
        )
    score = row.get("composite_score")
    last_score = cursor.get("last_score")
    try:
        score_f = None if score is None or pd.isna(score) else float(score)
        last_f = None if last_score is None else float(last_score)
    except (TypeError, ValueError):
        score_f, last_f = None, None
    if score_f is not None and last_f is not None and abs(score_f - last_f) >= SCORE_JUMP_THRESHOLD:
        delta = score_f - last_f
        out.append(
            AnalystEvent(
                kind=KIND_SCORE,
                uid=uid,
                ticker=ticker,
                period=f"{snapshot_date.isoformat()}|{last_f:.0f}->{score_f:.0f}",
                event_date=snapshot_date,
                title=f"Composite {_fmt_num(last_f, 1)} → {_fmt_num(score_f, 1)} ({'+' if delta > 0 else ''}{_fmt_num(delta, 1)})",
                payload={"from": last_f, "to": score_f, "delta": delta},
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Markt-Reaktion (deterministischer Block für den Prompt)
# --------------------------------------------------------------------------- #
def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(f) else f


def build_market_reaction(
    row, prices: pd.DataFrame | None, news_items: list[dict], report_date: date | None
) -> dict:
    """„Was hat der Markt schon verarbeitet“: Kursreaktion seit Bericht,
    Momentum/Revisionen aus der Zeile, Sentiment-Trend der News."""
    out: dict = {}
    if prices is not None and not prices.empty and report_date is not None and "adj_close" in prices.columns:
        s = prices["adj_close"].dropna()
        if not s.empty:
            idx = pd.to_datetime(s.index)
            s.index = idx
            after = s[idx >= pd.Timestamp(report_date)]
            if not after.empty:
                base = float(after.iloc[0])
                last = float(s.iloc[-1])
                if base > 0:
                    out["ret_since_report"] = round(last / base - 1.0, 4)
                    out["days_since_report"] = int((idx[-1] - pd.Timestamp(report_date)).days)
    for key in ("ret_1m", "ret_3m", "mom_12_1", "dist_52w_high", "eps_revisions_3m",
                "z_eps_revisions_3m", "z_momentum", "z_value"):
        val = _num(row.get(key)) if hasattr(row, "get") else None
        if val is not None:
            out[key] = round(val, 4)
    scored_items = [
        (i.get("time_published"), i.get("ticker_sentiment_score", i.get("sentiment")))
        for i in news_items or []
        if i.get("time_published") is not None
    ]
    scored_items = [(t, s) for t, s in scored_items if s is not None]
    if scored_items:
        scored_items.sort(key=lambda x: x[0])
        half = len(scored_items) // 2
        earlier = [s for _, s in scored_items[:half]]
        recent = [s for _, s in scored_items[half:]]
        if recent:
            out["news_sentiment_recent"] = round(sum(recent) / len(recent), 3)
        if earlier:
            out["news_sentiment_earlier"] = round(sum(earlier) / len(earlier), 3)
    out["n_news"] = len(news_items or [])
    return out
