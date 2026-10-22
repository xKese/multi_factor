"""KI-Chat: Frage-Antwort-Schleife mit Werkzeugaufrufen über Ollama Cloud.

:func:`answer` beantwortet eine Frage synchron (CLI, Tests);
:func:`start_answer` startet dieselbe Arbeit in einem Daemon-Thread und
hält je Sitzung einen Job-Status für das Dash-Panel (Polling über
:func:`job_status`). Alle Nachrichten (user, tool, assistant) landen in
``analyst_chat_messages``; der Verlauf der Sitzung wird beim nächsten
Aufruf als Kontext mitgegeben.

Modi (``settings.analyst_chat_mode``):

- ``tools``: Werkzeugaufrufe (Ollama-Tool-Calling); Modelle ohne
  Tool-Unterstützung liefern einen Fehler.
- ``context``: kein Tool-Calling; stattdessen ein deterministischer
  Daten-Dump (Seiten-Titel, erkannte Firmen, Universums-/Analyst-Übersicht,
  Methodik) als Kontext — funktioniert mit jedem Modell.
- ``auto``: ``tools``, bei „does not support tools“ Rückfall auf ``context``.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from app.core import av_client

from . import chat_tools as ct
from . import ollama_client
from . import store

log = logging.getLogger(__name__)

ollama = ollama_client  # Modulattribut zum Patchen in Tests

MODES = ("auto", "tools", "context")
MAX_PARALLEL_JOBS = 2
TOOL_MESSAGE_PERSIST_CHARS = 4000
SOURCES_HEADER = "**Quellen:**"

CHAT_SYSTEM_PROMPT = """Du bist der KI-Analyst des Investment-Komitees für das Meeder & Seifer Multi-Faktor-Modell (Composite v2: Value, Quality, Momentum, Investment; Zonen KANDIDAT / HALTEN / VERKAUFEN / FILTER). Du beantwortest Fragen zu Wertpapieren, Kapitalmärkten und zu allen Informationen des Tools (Koyfin-Import, Scores, Zonen, Verlauf, Portfolios, Modellportfolio, Factor Timing, Risiko, KI-Analyst-Läufe, Methodik).

Regeln:
- Antworte auf Deutsch in Markdown, präzise und strukturiert; Dezimalkomma bei Zahlen.
- Fakten und Zahlen nur aus den Werkzeugergebnissen oder dem mitgelieferten Kontext — nie erfinden. Fehlt eine Information, sag das und nenne, welches Werkzeug/welche Quelle sie liefern könnte.
- Zuerst die passenden Werkzeuge nutzen, dann antworten. Für Fragen zu einem Titel beginne mit company_snapshot (alle Koyfin-Kennzahlen, auch für europäische Titel, die Alpha Vantage nicht abdeckt); analyst_insights für gespeicherte Analysen; av_* für Live-News, Earnings, Transkripte, Insider, Kurse, Makro (US-zentriert — bei Nicht-US-Titeln auf Koyfin-Daten zurückgreifen).
- Z-Scores sind innerhalb der Neutralisierungsgruppe standardisiert: Z > 0 = besser als Gruppenmittel, ±1 ≈ eine Standardabweichung. Composite 0–100, höher = attraktiver. Zone FILTER = Universumsfilter nicht bestanden.
- Du lieferst Entscheidungsunterstützung, keine Anlageberatung und keine Handelsanweisung; weise bei Einschätzungen kurz auf Unsicherheiten hin.
- Beziehe dich bei Folgefragen auf den bisherigen Verlauf dieser Sitzung.
- Schließe mit einem Abschnitt „**Quellen:**“ ab (verwendete Werkzeuge/Datenstände, News-URLs).
"""

PROGRESS_LLM = "Modell denkt nach …"


@dataclass
class ChatResult:
    answer_md: str
    tool_trace: list[dict] = field(default_factory=list)
    n_llm_calls: int = 0
    n_av_calls: int = 0
    model: str = ""
    mode: str = "tools"
    sources: list[str] = field(default_factory=list)
    error: str | None = None
    session_id: int | None = None


class ChatConfigError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Hilfsfunktionen
# --------------------------------------------------------------------------- #
def resolve_model(settings, model: str | None = None) -> str:
    m = (model or "").strip() or str(getattr(settings, "analyst_chat_model", "") or "").strip()
    return m or str(getattr(settings, "analyst_model", "") or "gpt-oss:120b")


def resolve_mode(settings, mode: str | None = None) -> str:
    m = (mode or "").strip().lower() or str(getattr(settings, "analyst_chat_mode", "auto") or "auto").lower()
    return m if m in MODES else "auto"


def build_context(settings, scored, state, *, page_uid: str | None = None, use_page_context: bool = True,
                  today: date | None = None) -> ct.ChatContext:
    return ct.ChatContext(
        settings=settings,
        scored=scored,
        state=state,
        today=today or date.today(),
        page_uid=page_uid,
        use_page_context=bool(use_page_context),
        max_tool_chars=int(getattr(settings, "analyst_chat_max_tool_chars", 12000) or 12000),
        av_enabled=bool(av_client.api_key()),
        rpm=int(getattr(settings, "risk_av_requests_per_minute", 70) or 70),
    )


def _page_context_line(ctx: ct.ChatContext) -> str:
    if not (ctx.use_page_context and ctx.page_uid):
        return ""
    from app.core.uid import row_by_uid

    row = row_by_uid(ctx.scored, ctx.page_uid)
    if row is None:
        return ""
    return (f"Aktueller Seitenbezug: {row.get('ticker')} — {row.get('name')} (uid {row.get('uid')}, "
            f"Zone {row.get('zone_v2') or '–'}). Fragen ohne expliziten Titel beziehen sich auf diesen Titel.")


def system_message(ctx: ct.ChatContext, mode: str) -> dict:
    parts = [CHAT_SYSTEM_PROMPT, f"Heutiges Datum: {ctx.today.isoformat()}."]
    n = len(ctx.scored) if ctx.scored is not None else 0
    parts.append(f"Universum: {n} Titel geladen.")
    if not ctx.av_enabled:
        parts.append("Alpha-Vantage-Live-Daten sind in dieser Umgebung nicht verfügbar (kein API-Key).")
    page = _page_context_line(ctx)
    if page:
        parts.append(page)
    if mode == "context":
        parts.append("In dieser Sitzung stehen keine Werkzeuge zur Verfügung; nutze ausschließlich den Abschnitt „Kontextdaten“ in der Nutzernachricht.")
    return {"role": "system", "content": "\n\n".join(parts)}


def history_messages(session_id: int, limit: int) -> list[dict]:
    """Letzte ``limit`` user/assistant-Nachrichten der Sitzung (ohne Quellen-Footer)."""
    if limit <= 0:
        return []
    rows = store.load_chat_messages(session_id, limit=limit, roles=("user", "assistant"))
    out = []
    for r in rows:
        content = str(r.get("content") or "")
        if r.get("role") == "assistant":
            content = strip_sources(content)
        out.append({"role": r["role"], "content": content})
    return out


def strip_sources(text: str) -> str:
    idx = text.rfind(SOURCES_HEADER)
    return text[:idx].rstrip() if idx > 0 else text


def append_sources(text: str, sources: list[str]) -> str:
    if not sources or SOURCES_HEADER in text or "Quellen:" in text[-1500:]:
        return text
    return text.rstrip() + "\n\n" + SOURCES_HEADER + "\n" + "\n".join(f"- {s}" for s in sources)


def session_title_from_question(question: str, max_len: int = 60) -> str:
    q = re.sub(r"\s+", " ", str(question or "")).strip()
    if not q:
        return "Neue Unterhaltung"
    return q if len(q) <= max_len else q[: max_len - 1].rstrip() + "…"


def _coerce_args(value) -> dict:
    """Werkzeug-Argumente als Dict (JSON-Strings werden geparst, Unsinn → {})."""
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _format_call(tc: dict) -> str:
    args = _coerce_args(tc.get("arguments"))
    short = ", ".join(f"{k}={str(v)[:24]}" for k, v in list(args.items())[:3])
    return f"{tc.get('name')}({short})"


# --------------------------------------------------------------------------- #
# Kontextmodus (kein Tool-Calling)
# --------------------------------------------------------------------------- #
_STOPWORDS = {"wie", "was", "ist", "der", "die", "das", "und", "oder", "mit", "von", "für", "fuer", "bei", "ein", "eine",
              "im", "in", "am", "zu", "zum", "zur", "auf", "aus", "den", "dem", "des", "ich", "wir", "sie", "er", "es",
              "nicht", "kann", "sind", "hat", "haben", "sich", "welche", "welcher", "welches", "warum", "wann", "wo",
              "aktie", "aktien", "titel", "unternehmen", "firma", "modell", "score", "zone", "news", "the", "and"}


def detect_companies(ctx: ct.ChatContext, question: str, max_n: int = 3) -> list[str]:
    """Bis zu ``max_n`` uids, die in der Frage erkennbar sind (Ticker exakt
    oder Namens-Prefix ≥ 4 Zeichen)."""
    found: list[str] = []
    if ctx.scored is None or ctx.scored.empty:
        return found
    tokens = re.findall(r"[A-Za-zÄÖÜäöüß0-9~\-\.]+", str(question or ""))
    for tok in tokens:
        t = tok.strip(".,;:!?")
        if len(t) < 2 or t.lower() in _STOPWORDS:
            continue
        hits = ct.resolve_uid(ctx, t, limit=3)
        if len(hits) == 1 and hits[0]["uid"] not in found:
            # Namens-Teiltreffer nur ab 4 Zeichen (sonst zu viele Zufallstreffer)
            if hits[0]["ticker"].upper() == t.upper() or hits[0]["uid"] == t or len(t) >= 4:
                found.append(hits[0]["uid"])
        if len(found) >= max_n:
            break
    return found


def build_context_dump(ctx: ct.ChatContext, question: str) -> str:
    """Deterministischer Datenblock für den Kontextmodus."""
    uids: list[str] = []
    if ctx.use_page_context and ctx.page_uid:
        uids.append(ctx.page_uid)
    for u in detect_companies(ctx, question):
        if u not in uids:
            uids.append(u)
    parts: list[str] = []
    per_company = max(2500, (ctx.max_tool_chars // max(1, len(uids))) if uids else ctx.max_tool_chars)
    for uid in uids[:4]:
        snapshot = ct.run_tool("company_snapshot", {"uid": uid}, ctx)
        parts.append(f"## Titel {uid}\n\n{snapshot[:per_company]}")
        insights = ct.run_tool("analyst_insights", {"uid": uid}, ctx)
        if "Noch keine Analyst-Ergebnisse" not in insights:
            parts.append(insights[: per_company // 2])
    parts.append("## Universum\n\n" + ct.run_tool("universe_overview", {"top_n": 10}, ctx)[:4000])
    runs = ct.run_tool("analyst_run_overview", {"n_runs": 2}, ctx)
    parts.append("## KI-Analyst\n\n" + runs[:3000])
    parts.append("## Methodik\n\n" + ct.run_tool("methodology", {"topic": "alle"}, ctx)[:3000])
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# Hauptschleife
# --------------------------------------------------------------------------- #
def answer(
    session_id: int,
    question: str,
    ctx: ct.ChatContext,
    settings,
    *,
    model: str | None = None,
    mode: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> ChatResult:
    """Beantwortet ``question`` in Sitzung ``session_id`` und persistiert
    user-, tool- und assistant-Nachrichten. Raised nur bei fehlendem
    Ollama-Key (``ChatConfigError``); LLM-Fehler werden als Antwort mit
    ``error`` zurückgegeben und ebenfalls persistiert."""
    question = str(question or "").strip()
    if not question:
        raise ValueError("Leere Frage.")
    if not ollama.api_key():
        raise ChatConfigError("OLLAMA_API_KEY ist nicht gesetzt — KI-Chat nicht verfügbar.")
    model_name = resolve_model(settings, model)
    chosen_mode = resolve_mode(settings, mode)
    max_rounds = max(1, int(getattr(settings, "analyst_chat_max_tool_rounds", 8) or 8))
    history_n = int(getattr(settings, "analyst_chat_history_messages", 20) or 0)
    timeout = int(getattr(settings, "analyst_llm_timeout_seconds", 300) or 300)
    notify = progress or (lambda msg: None)

    history = history_messages(session_id, history_n)
    store.append_chat_message(session_id, "user", question)

    result = ChatResult(answer_md="", model=model_name, mode="tools" if chosen_mode == "auto" else chosen_mode, session_id=session_id)
    try:
        if chosen_mode in ("auto", "tools"):
            try:
                _run_tool_loop(result, session_id, question, ctx, history, model_name, max_rounds, timeout, notify)
            except ollama_client.OllamaError as exc:
                if chosen_mode == "auto" and ollama.tools_unsupported(exc):
                    log.info("Modell %s ohne Tool-Unterstützung — Kontextmodus.", model_name)
                    result.mode = "context"
                    result.tool_trace = []
                    _run_context_mode(result, question, ctx, history, model_name, timeout, notify)
                else:
                    raise
        else:
            _run_context_mode(result, question, ctx, history, model_name, timeout, notify)
    except Exception as exc:  # noqa: BLE001
        log.warning("KI-Chat fehlgeschlagen: %s", exc)
        result.error = f"{type(exc).__name__}: {exc}"
        result.answer_md = f"**Fehler:** {exc}\n\nBitte Modell/Modus in den Einstellungen prüfen oder später erneut versuchen."
    result.sources = list(ctx.sources)
    result.n_av_calls = ctx.n_av_calls
    if not result.error:
        result.answer_md = append_sources(result.answer_md, result.sources)
    result.tool_trace = list(ctx.trace)
    try:
        store.append_chat_message(
            session_id, "assistant", result.answer_md, model=model_name,
            n_llm_calls=result.n_llm_calls, n_av_calls=result.n_av_calls,
            trace={"mode": result.mode, "tools": result.tool_trace, "error": result.error},
        )
        store.touch_chat_session(session_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Chat-Antwort konnte nicht gespeichert werden: %s", exc)
    return result


def _run_tool_loop(result: ChatResult, session_id: int, question: str, ctx: ct.ChatContext, history: list[dict],
                   model_name: str, max_rounds: int, timeout: int, notify) -> None:
    tools = ct.tool_specs(include_live=ctx.av_enabled)
    messages = [system_message(ctx, "tools"), *history, {"role": "user", "content": question}]
    final: str | None = None
    for _round in range(max_rounds):
        notify(PROGRESS_LLM)
        msg = ollama.chat_tools(messages, model_name, tools=tools, timeout=timeout)
        result.n_llm_calls += 1
        calls = msg.get("tool_calls") or []
        if not calls:
            final = msg.get("content") or ""
            break
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": msg.get("raw_tool_calls") or []})
        for tc in calls:
            notify(f"Werkzeug {_format_call(tc)} …")
            args = _coerce_args(tc.get("arguments"))
            out = ct.run_tool(tc["name"], args, ctx)
            messages.append({"role": "tool", "content": out, "tool_name": tc["name"]})
            try:
                store.append_chat_message(session_id, "tool", out[:TOOL_MESSAGE_PERSIST_CHARS], tool_name=tc["name"],
                                          tool_calls={"arguments": args})
            except Exception as exc:  # noqa: BLE001
                log.warning("Tool-Nachricht nicht gespeichert: %s", exc)
    if final is None:
        messages.append({"role": "user", "content": "Das Werkzeug-Limit ist erreicht. Fasse jetzt die vorliegenden Ergebnisse zur Antwort zusammen und nenne, was offen bleibt."})
        notify(PROGRESS_LLM)
        msg = ollama.chat_tools(messages, model_name, tools=None, timeout=timeout)
        result.n_llm_calls += 1
        final = msg.get("content") or ""
    result.answer_md = final.strip()


def _run_context_mode(result: ChatResult, question: str, ctx: ct.ChatContext, history: list[dict],
                      model_name: str, timeout: int, notify) -> None:
    notify("Kontextdaten werden zusammengestellt …")
    dump = build_context_dump(ctx, question)
    user = f"{question}\n\n---\n\n# Kontextdaten (aus dem Tool)\n\n{dump}"
    messages = [system_message(ctx, "context"), *history, {"role": "user", "content": user}]
    notify(PROGRESS_LLM)
    msg = ollama.chat_tools(messages, model_name, tools=None, timeout=timeout)
    result.n_llm_calls += 1
    result.answer_md = (msg.get("content") or "").strip()


# --------------------------------------------------------------------------- #
# Job-Registry (Dash)
# --------------------------------------------------------------------------- #
_JOBS: dict[int, dict] = {}
_JOBS_LOCK = threading.Lock()


def _set_job(session_id: int, **updates) -> None:
    with _JOBS_LOCK:
        _JOBS.setdefault(int(session_id), {}).update(updates)


def job_status(session_id: int | None) -> dict | None:
    if session_id is None:
        return None
    with _JOBS_LOCK:
        job = _JOBS.get(int(session_id))
        return dict(job) if job else None


def running_jobs() -> int:
    with _JOBS_LOCK:
        return sum(1 for j in _JOBS.values() if j.get("status") == "running")


def start_answer(
    session_id: int,
    question: str,
    *,
    page_uid: str | None = None,
    use_page_context: bool = True,
    model: str | None = None,
    mode: str | None = None,
    settings=None,
    scored=None,
    state=None,
) -> tuple[bool, str]:
    """Startet die Beantwortung im Hintergrund. ``(ok, meldung)``."""
    question = str(question or "").strip()
    if not question:
        return False, "Bitte eine Frage eingeben."
    if not ollama.api_key():
        return False, "OLLAMA_API_KEY ist nicht gesetzt — KI-Chat nicht verfügbar."
    if settings is None or scored is None or state is None:
        from app.core.state import STATE

        settings = settings or STATE.settings
        scored = scored if scored is not None else STATE.scored
        state = state or STATE
    if scored is None or getattr(scored, "empty", True):
        return False, "Kein Universum geladen — bitte zuerst einen Koyfin-Export importieren."
    with _JOBS_LOCK:
        job = _JOBS.get(int(session_id))
        if job and job.get("status") == "running":
            return False, "In dieser Sitzung läuft bereits eine Anfrage."
        if sum(1 for j in _JOBS.values() if j.get("status") == "running") >= MAX_PARALLEL_JOBS:
            return False, "Es laufen bereits zu viele Chat-Anfragen — bitte kurz warten."
        _JOBS[int(session_id)] = {
            "status": "running", "question": question, "progress": "Gestartet …",
            "started_at": time.time(), "finished_at": None, "error": None, "seq": int(job.get("seq", 0)) + 1 if job else 1,
        }
    ctx = build_context(settings, scored, state, page_uid=page_uid, use_page_context=use_page_context)

    def _run() -> None:
        try:
            res = answer(session_id, question, ctx, settings, model=model, mode=mode,
                         progress=lambda m: _set_job(session_id, progress=m))
            _set_job(session_id, status="error" if res.error else "done", error=res.error,
                     finished_at=time.time(), progress="", n_llm_calls=res.n_llm_calls, n_av_calls=res.n_av_calls)
        except Exception as exc:  # noqa: BLE001
            log.warning("Chat-Job fehlgeschlagen: %s", exc, exc_info=True)
            try:
                store.append_chat_message(session_id, "assistant", f"**Fehler:** {exc}", trace={"error": str(exc)})
            except Exception:  # noqa: BLE001
                pass
            _set_job(session_id, status="error", error=str(exc), finished_at=time.time(), progress="")

    threading.Thread(target=_run, name=f"ki-chat-{session_id}", daemon=True).start()
    return True, "Anfrage gestartet."
