"""HTTP-Client für die Ollama-Cloud-API (``https://ollama.com``).

Spiegelt die Disziplin von :mod:`app.core.av_client`: ``requests`` und
``time`` sind Modulattribute (Tests ersetzen sie per monkeypatch), der
API-Key kommt ausschließlich aus ``OLLAMA_API_KEY`` und wird nie geloggt,
Fehler werden als :class:`OllamaError` mit ``kind`` gemeldet.

Verwendet wird die native Chat-API (``POST /api/chat``, ``stream: false``)
mit ``format`` = JSON-Schema. Die Cloud erzwingt das Schema nicht in jedem
Fall, daher parst :func:`chat_json` defensiv (Code-Fences, umgebender
Text) und wiederholt bei ungültigem JSON genau einmal mit einem Hinweis.
"""

from __future__ import annotations

import json
import logging
import os
import time

import requests

log = logging.getLogger(__name__)

BASE_URL_DEFAULT = "https://ollama.com"
RETRY_DELAYS: tuple[float, ...] = (2.0, 8.0, 30.0)
_CONNECT_TIMEOUT = 10
_TAGS_TIMEOUT = (5, 20)

_JSON_RETRY_HINT = (
    "Deine vorherige Antwort war kein gültiges JSON-Objekt gemäß Schema. "
    "Antworte jetzt ausschließlich mit dem JSON-Objekt — ohne Erklärung, "
    "ohne Markdown-Zaun, ohne Text davor oder danach."
)


class OllamaError(RuntimeError):
    """``kind``: ``auth`` (401/403), ``rate_limit`` (429 trotz Retries),
    ``network`` (Verbindung), ``error`` (sonstige API-Fehler),
    ``parse`` (Antwort ohne Inhalt)."""

    def __init__(self, message: str, kind: str = "error") -> None:
        super().__init__(message)
        self.kind = kind


def api_key() -> str | None:
    key = os.getenv("OLLAMA_API_KEY", "").strip()
    return key or None


def base_url() -> str:
    return (os.getenv("OLLAMA_BASE_URL", "").strip() or BASE_URL_DEFAULT).rstrip("/")


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post(path: str, body: dict, timeout: int) -> dict:
    """POST mit Retry bei Netzwerkfehlern, 429 und 5xx; 401/403 sofort."""
    key = api_key()
    if not key:
        raise OllamaError("OLLAMA_API_KEY ist nicht gesetzt", kind="auth")
    url = f"{base_url()}{path}"
    last_message = "unbekannter Fehler"
    last_kind = "error"
    attempts = len(RETRY_DELAYS) + 1
    for attempt in range(attempts):
        if attempt:
            time.sleep(RETRY_DELAYS[attempt - 1])
        try:
            resp = requests.post(
                url, json=body, headers=_headers(key), timeout=(_CONNECT_TIMEOUT, timeout)
            )
        except requests.RequestException as exc:
            last_message = f"Netzwerkfehler: {exc.__class__.__name__}"
            last_kind = "network"
            log.info("Ollama-Request fehlgeschlagen (Versuch %d): %s", attempt + 1, last_message)
            continue
        status = resp.status_code
        if status in (401, 403):
            raise OllamaError(f"Ollama: Zugriff verweigert (HTTP {status})", kind="auth")
        if status == 429:
            last_message = "Rate-Limit (HTTP 429)"
            last_kind = "rate_limit"
            log.info("Ollama-Rate-Limit (Versuch %d)", attempt + 1)
            continue
        if status >= 500:
            last_message = f"HTTP {status}"
            last_kind = "error"
            log.info("Ollama-Serverfehler %s (Versuch %d)", status, attempt + 1)
            continue
        if status != 200:
            raise OllamaError(f"Ollama: HTTP {status}", kind="error")
        try:
            data = resp.json()
        except ValueError as exc:
            raise OllamaError(f"Ollama: Antwort ist kein JSON ({exc})", kind="parse")
        if not isinstance(data, dict):
            raise OllamaError("Ollama: Antwort ist kein JSON-Objekt", kind="parse")
        return data
    raise OllamaError(
        f"Ollama-Request nach {attempts} Versuchen aufgegeben — {last_message}",
        kind=last_kind,
    )


def chat(
    messages: list[dict],
    model: str,
    schema: dict | None = None,
    temperature: float = 0.0,
    timeout: int = 300,
) -> str:
    """Ein Chat-Aufruf; liefert den Antworttext (``message.content``)."""
    body: dict = {
        "model": model,
        "messages": messages,
        "stream": False,
        "format": schema if schema is not None else "json",
        "options": {"temperature": float(temperature)},
    }
    data = _post("/api/chat", body, timeout)
    message = data.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise OllamaError("Ollama: leere Antwort", kind="parse")
    return content


def parse_json_response(text: str | None) -> dict | None:
    """Robustes Parsen: Code-Fences entfernen, auf ``{…}`` zuschneiden."""
    if not isinstance(text, str):
        return None
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_nl = cleaned.find("\n")
        cleaned = cleaned[first_nl + 1 :] if first_nl >= 0 else ""
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def chat_json(
    system: str,
    user: str,
    schema: dict,
    required_keys: tuple[str, ...] | list[str] = (),
    model: str = "",
    temperature: float = 0.0,
    timeout: int = 300,
) -> tuple[dict | None, str, int]:
    """Chat mit JSON-Schema; genau ein Retry bei ungültigem Ergebnis.

    Rückgabe ``(parsed | None, raw_text, n_calls)``. ``parsed`` ist ``None``,
    wenn auch der Retry kein JSON-Objekt mit allen ``required_keys``
    liefert — der Aufrufer speichert dann den Rohtext als ``unparsed``.
    """
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    raw = chat(messages, model, schema=schema, temperature=temperature, timeout=timeout)
    n_calls = 1
    parsed = parse_json_response(raw)
    if parsed is not None and all(k in parsed for k in required_keys):
        return parsed, raw, n_calls
    retry_messages = messages + [
        {"role": "assistant", "content": raw},
        {"role": "user", "content": _JSON_RETRY_HINT},
    ]
    raw2 = chat(retry_messages, model, schema=schema, temperature=temperature, timeout=timeout)
    n_calls += 1
    parsed2 = parse_json_response(raw2)
    if parsed2 is not None and all(k in parsed2 for k in required_keys):
        return parsed2, raw2, n_calls
    return None, raw2, n_calls


def list_models() -> list[str]:
    """Modellnamen der Cloud (``GET /api/tags``); fail-open ``[]``."""
    key = api_key()
    if not key:
        return []
    try:
        resp = requests.get(
            f"{base_url()}/api/tags", headers=_headers(key), timeout=_TAGS_TIMEOUT
        )
        if resp.status_code != 200:
            return []
        data = resp.json()
    except (requests.RequestException, ValueError) as exc:
        log.info("Ollama-Modellliste nicht erreichbar: %s", exc.__class__.__name__)
        return []
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return []
    names = []
    for m in models:
        name = m.get("name") or m.get("model") if isinstance(m, dict) else None
        if isinstance(name, str) and name:
            names.append(name)
    return sorted(set(names))
