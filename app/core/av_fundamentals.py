"""Alpha-Vantage-Fundamental- und News-Endpunkte für den KI-Analysten.

Ergänzt :mod:`app.core.av_client` (Kurse/FX/Makro) um die fünf Endpunkte,
die der Analyst-Agent braucht:

- ``EARNINGS`` — Quartals-EPS mit Schätzung und Überraschung
- ``EARNINGS_CALL_TRANSCRIPT`` — Transkript-Segmente je Quartal
- ``EARNINGS_CALENDAR`` — kommende Berichtstermine (liefert **CSV**)
- ``NEWS_SENTIMENT`` — News-Feed mit Sentiment, je Ticker **oder** Topic
- ``INSIDER_TRANSACTIONS`` — Insider-Käufe/-Verkäufe

Transport, Rate-Limit, Retry und Fehlererkennung kommen unverändert aus
``av_client`` (``_get``, ``_limiter``, ``api_key``, ``AlphaVantageError``);
der CSV-Endpunkt braucht einen eigenen Text-GET, weil ``_get`` das
JSON-Format erzwingt und ein leeres ``{}`` als Rate-Limit lesen würde.

Alle Normalisierer sind defensiv: AV liefert Zahlen als Strings, fehlende
Werte als ``"None"`` und bei unbekannten Symbolen ein leeres Objekt — das
sind „keine Daten", keine Fehler. ``av_client.requests`` bleibt der einzige
HTTP-Zugang, damit Tests ihn an einer Stelle ersetzen können.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import date, datetime, timedelta

from . import av_client
from .av_client import AlphaVantageError

log = logging.getLogger(__name__)

# Alpha Vantage liefert Zeitstempel im Feed als ``YYYYMMDDTHHMMSS``.
_NEWS_TS_FORMAT = "%Y%m%dT%H%M%S"


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def _float(value) -> float | None:
    """``"12.3"`` → 12.3; ``"None"``/``""``/``None`` → ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "-", "."}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _date(value) -> date | None:
    """``"2026-04-30"`` → date; alles andere → ``None``."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null"}:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _text(value) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    return "" if text.lower() == "none" else text


def quarter_label(d: date) -> str:
    """Kalenderquartal einer Periode im AV-Format, z. B. ``2026Q3``."""
    return f"{d.year}Q{(d.month - 1) // 3 + 1}"


def latest_reported_quarter(earnings: list[dict]) -> tuple[str, date] | None:
    """Jüngstes berichtetes Quartal ``(label, reported_date)`` aus den
    Earnings-Zeilen (Fiskalquartal-Ende bestimmt das Label)."""
    best: tuple[str, date] | None = None
    for row in earnings:
        fiscal = row.get("fiscal_date_ending")
        reported = row.get("reported_date")
        if fiscal is None or reported is None:
            continue
        if best is None or reported > best[1]:
            best = (quarter_label(fiscal), reported)
    return best


def _get_text(params: dict, requests_per_minute: int) -> str:
    """GET für Endpunkte, die CSV statt JSON liefern (EARNINGS_CALENDAR).

    Gleiche Retry-/Limit-Disziplin wie ``av_client._get``; ein JSON-Body
    (Fehler-/Limit-Hinweis) wird erkannt und als ``AlphaVantageError``
    gemeldet.
    """
    key = av_client.api_key()
    if not key:
        raise AlphaVantageError("ALPHAVANTAGE_API_KEY ist nicht gesetzt", kind="error")

    query = {**params, "apikey": key}
    last_message = "unbekannter Fehler"
    attempts = len(av_client.RETRY_DELAYS) + 1
    for attempt in range(attempts):
        if attempt:
            av_client.time.sleep(av_client.RETRY_DELAYS[attempt - 1])
        av_client._limiter.wait(requests_per_minute)
        try:
            resp = av_client.requests.get(
                av_client.BASE_URL, params=query, timeout=av_client._TIMEOUT
            )
        except av_client.requests.RequestException as exc:
            last_message = f"Netzwerkfehler: {exc}"
            continue
        if resp.status_code != 200:
            last_message = f"HTTP {resp.status_code}"
            continue
        body = resp.text if isinstance(getattr(resp, "text", None), str) else ""
        stripped = body.lstrip()
        if stripped.startswith("{"):
            # Fehler-/Limit-Antwort im JSON-Format.
            lowered = stripped.lower()
            if '"error message"' in lowered:
                raise AlphaVantageError(
                    f"API-Fehler für {params.get('function')}: {stripped[:200]}",
                    kind="error",
                )
            last_message = f"Rate-Limit: {stripped[:200]}"
            continue
        return body

    raise AlphaVantageError(
        f"AV-Request nach {attempts} Versuchen aufgegeben — {last_message}",
        kind="rate_limit" if "Rate-Limit" in last_message else "error",
    )


# --------------------------------------------------------------------------- #
# EARNINGS
# --------------------------------------------------------------------------- #
def fetch_earnings(symbol: str, requests_per_minute: int = 70) -> list[dict]:
    """Quartals-EPS eines Symbols, neueste Berichte zuerst.

    Zeilen: ``fiscal_date_ending`` (date), ``reported_date`` (date | None),
    ``reported_eps``, ``estimated_eps``, ``surprise``, ``surprise_pct``.
    Unbekanntes Symbol (leeres Objekt) → ``[]``.
    """
    data = av_client._get(
        {"function": "EARNINGS", "symbol": symbol}, requests_per_minute
    )
    quarters = data.get("quarterlyEarnings")
    if not isinstance(quarters, list):
        return []
    rows: list[dict] = []
    for q in quarters:
        if not isinstance(q, dict):
            continue
        fiscal = _date(q.get("fiscalDateEnding"))
        if fiscal is None:
            continue
        rows.append(
            {
                "fiscal_date_ending": fiscal,
                "reported_date": _date(q.get("reportedDate")),
                "reported_eps": _float(q.get("reportedEPS")),
                "estimated_eps": _float(q.get("estimatedEPS")),
                "surprise": _float(q.get("surprise")),
                "surprise_pct": _float(q.get("surprisePercentage")),
            }
        )
    rows.sort(
        key=lambda r: (r["reported_date"] or r["fiscal_date_ending"]), reverse=True
    )
    return rows


# --------------------------------------------------------------------------- #
# EARNINGS_CALL_TRANSCRIPT
# --------------------------------------------------------------------------- #
def fetch_transcript(
    symbol: str, quarter: str, requests_per_minute: int = 70
) -> dict | None:
    """Earnings-Call-Transkript eines Quartals (``YYYYQn``) oder ``None``,
    wenn AV keines liefert (typisch bei Nicht-US-Titeln).

    Rückgabe: ``{"symbol", "quarter", "segments": [{"speaker", "title",
    "content", "sentiment"}]}``; leere Segmente werden übersprungen.
    """
    data = av_client._get(
        {
            "function": "EARNINGS_CALL_TRANSCRIPT",
            "symbol": symbol,
            "quarter": quarter,
        },
        requests_per_minute,
    )
    raw = data.get("transcript")
    if not isinstance(raw, list) or not raw:
        return None
    segments: list[dict] = []
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        content = _text(seg.get("content"))
        if not content:
            continue
        segments.append(
            {
                "speaker": _text(seg.get("speaker")),
                "title": _text(seg.get("title")),
                "content": content,
                "sentiment": _float(seg.get("sentiment")),
            }
        )
    if not segments:
        return None
    return {
        "symbol": _text(data.get("symbol")) or str(symbol),
        "quarter": _text(data.get("quarter")) or str(quarter),
        "segments": segments,
    }


# --------------------------------------------------------------------------- #
# EARNINGS_CALENDAR (CSV)
# --------------------------------------------------------------------------- #
def fetch_earnings_calendar(
    symbol: str | None = None,
    horizon: str = "3month",
    requests_per_minute: int = 70,
) -> list[dict]:
    """Kommende Berichtstermine (CSV-Endpunkt).

    Ohne ``symbol`` die vollständige Liste (ein Call je Lauf, danach auf
    die Watchlist gefiltert). Zeilen: ``symbol, name, report_date (date),
    fiscal_date_ending (date | None), estimate (float | None), currency``.
    """
    params: dict = {"function": "EARNINGS_CALENDAR", "horizon": horizon}
    if symbol:
        params["symbol"] = symbol
    body = _get_text(params, requests_per_minute)
    rows: list[dict] = []
    reader = csv.DictReader(io.StringIO(body))
    for rec in reader:
        report = _date(rec.get("reportDate"))
        sym = _text(rec.get("symbol"))
        if report is None or not sym:
            continue
        rows.append(
            {
                "symbol": sym,
                "name": _text(rec.get("name")),
                "report_date": report,
                "fiscal_date_ending": _date(rec.get("fiscalDateEnding")),
                "estimate": _float(rec.get("estimate")),
                "currency": _text(rec.get("currency")),
            }
        )
    rows.sort(key=lambda r: r["report_date"])
    return rows


# --------------------------------------------------------------------------- #
# NEWS_SENTIMENT
# --------------------------------------------------------------------------- #
def _parse_news_ts(value) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    try:
        return datetime.strptime(text, _NEWS_TS_FORMAT)
    except ValueError:
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None


def format_news_time(dt: datetime) -> str:
    """``time_from``/``time_to``-Format der API (``YYYYMMDDTHHMM``)."""
    return dt.strftime("%Y%m%dT%H%M")


def fetch_news_sentiment(
    tickers: list[str] | None = None,
    topics: list[str] | None = None,
    time_from: datetime | None = None,
    limit: int = 50,
    sort: str = "LATEST",
    requests_per_minute: int = 70,
) -> list[dict]:
    """News-Feed mit Sentiment, gefiltert nach Tickern **oder** Topics.

    Mindestens eines von ``tickers``/``topics`` ist Pflicht. Items:
    ``title, url, time_published (datetime | None), summary, source,
    overall_sentiment_score, overall_sentiment_label,
    ticker_sentiment [{ticker, relevance_score, sentiment_score,
    sentiment_label}], topics [{topic, relevance_score}]``.
    """
    if not tickers and not topics:
        raise ValueError("fetch_news_sentiment: tickers oder topics erforderlich")
    params: dict = {
        "function": "NEWS_SENTIMENT",
        "limit": int(limit),
        "sort": sort,
    }
    if tickers:
        params["tickers"] = ",".join(str(t) for t in tickers if t)
    if topics:
        params["topics"] = ",".join(str(t) for t in topics if t)
    if time_from is not None:
        params["time_from"] = format_news_time(time_from)
    data = av_client._get(params, requests_per_minute)
    feed = data.get("feed")
    if not isinstance(feed, list):
        return []
    items: list[dict] = []
    for entry in feed:
        if not isinstance(entry, dict):
            continue
        title = _text(entry.get("title"))
        if not title:
            continue
        ts_list = []
        for ts in entry.get("ticker_sentiment") or []:
            if not isinstance(ts, dict):
                continue
            ts_list.append(
                {
                    "ticker": _text(ts.get("ticker")),
                    "relevance_score": _float(ts.get("relevance_score")) or 0.0,
                    "sentiment_score": _float(ts.get("ticker_sentiment_score")),
                    "sentiment_label": _text(ts.get("ticker_sentiment_label")),
                }
            )
        topic_list = []
        for tp in entry.get("topics") or []:
            if not isinstance(tp, dict):
                continue
            topic_list.append(
                {
                    "topic": _text(tp.get("topic")),
                    "relevance_score": _float(tp.get("relevance_score")) or 0.0,
                }
            )
        items.append(
            {
                "title": title,
                "url": _text(entry.get("url")),
                "time_published": _parse_news_ts(entry.get("time_published")),
                "summary": _text(entry.get("summary")),
                "source": _text(entry.get("source")),
                "overall_sentiment_score": _float(entry.get("overall_sentiment_score")),
                "overall_sentiment_label": _text(entry.get("overall_sentiment_label")),
                "ticker_sentiment": ts_list,
                "topics": topic_list,
            }
        )
    return items


# --------------------------------------------------------------------------- #
# INSIDER_TRANSACTIONS
# --------------------------------------------------------------------------- #
def fetch_insider_transactions(
    symbol: str,
    since: date | None = None,
    requests_per_minute: int = 70,
) -> list[dict]:
    """Insider-Transaktionen (neueste zuerst), optional ab ``since``
    (clientseitiger Filter — die API filtert nicht zuverlässig).

    Zeilen: ``transaction_date (date), executive, executive_title,
    security_type, acquisition_or_disposal ("A" | "D"), shares, share_price``.
    """
    data = av_client._get(
        {"function": "INSIDER_TRANSACTIONS", "symbol": symbol},
        requests_per_minute,
    )
    raw = data.get("data")
    if not isinstance(raw, list):
        return []
    rows: list[dict] = []
    for rec in raw:
        if not isinstance(rec, dict):
            continue
        tx_date = _date(rec.get("transaction_date"))
        if tx_date is None:
            continue
        if since is not None and tx_date < since:
            continue
        rows.append(
            {
                "transaction_date": tx_date,
                "executive": _text(rec.get("executive")),
                "executive_title": _text(rec.get("executive_title")),
                "security_type": _text(rec.get("security_type")),
                "acquisition_or_disposal": _text(rec.get("acquisition_or_disposal")).upper()[:1],
                "shares": _float(rec.get("shares")),
                "share_price": _float(rec.get("share_price")),
            }
        )
    rows.sort(key=lambda r: r["transaction_date"], reverse=True)
    return rows


def default_news_window(now: datetime, days: int) -> datetime:
    """Startzeitpunkt des News-Fensters (Erstlauf): ``now − days``."""
    return now - timedelta(days=max(1, int(days)))
