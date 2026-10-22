"""Tests für den Ollama-Cloud-Client (Transport, Retry, JSON-Parsing)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests as real_requests

from app.core.analyst import ollama_client as oc

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class _FakeResponse:
    def __init__(self, payload, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


@pytest.fixture(autouse=True)
def _fast_and_keyed(monkeypatch):
    monkeypatch.setenv("OLLAMA_API_KEY", "ollama-secret")
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.setattr(oc.time, "sleep", lambda *_: None)


def _fake_post(responses: list):
    calls: list[dict] = []

    def post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "json": json, "headers": headers})
        item = responses[min(len(calls) - 1, len(responses) - 1)]
        if isinstance(item, Exception):
            raise item
        return item

    post.calls = calls
    return post


def test_chat_json_happy_path(monkeypatch):
    fake = _fake_post([_FakeResponse(_load("ollama_chat_sample.json"))])
    monkeypatch.setattr(oc.requests, "post", fake)

    parsed, raw, n = oc.chat_json(
        "sys", "user", {"type": "object"}, ("zusammenfassung",), model="gpt-oss:120b"
    )

    assert n == 1
    assert parsed["ton"] == 4
    call = fake.calls[0]
    assert call["url"] == "https://ollama.com/api/chat"
    assert call["headers"]["Authorization"] == "Bearer ollama-secret"
    assert call["json"]["model"] == "gpt-oss:120b"
    assert call["json"]["stream"] is False
    assert call["json"]["format"] == {"type": "object"}
    assert call["json"]["options"]["temperature"] == 0.0
    assert call["json"]["messages"][0] == {"role": "system", "content": "sys"}


def test_parse_json_response_strips_fences():
    content = _load("ollama_chat_fenced.json")["message"]["content"]
    assert oc.parse_json_response(content) == {"zusammenfassung": "Mit Zaun.", "ton": 3}
    assert oc.parse_json_response("kein json") is None
    assert oc.parse_json_response("[1, 2]") is None
    assert oc.parse_json_response(None) is None


def test_chat_json_retries_once_on_missing_key(monkeypatch):
    bad = _FakeResponse({"message": {"role": "assistant", "content": "{\"foo\": 1}"}})
    fake = _fake_post([bad, bad])
    monkeypatch.setattr(oc.requests, "post", fake)

    parsed, raw, n = oc.chat_json("s", "u", {}, ("zusammenfassung",), model="m")

    assert parsed is None and n == 2
    assert raw == "{\"foo\": 1}"
    # Retry trägt die vorherige Antwort und den Hinweis mit.
    msgs = fake.calls[1]["json"]["messages"]
    assert msgs[-2]["role"] == "assistant"
    assert "JSON" in msgs[-1]["content"]


def test_chat_json_retry_succeeds(monkeypatch):
    bad = _FakeResponse({"message": {"role": "assistant", "content": "Sorry, no."}})
    good = _FakeResponse(_load("ollama_chat_sample.json"))
    monkeypatch.setattr(oc.requests, "post", _fake_post([bad, good]))
    parsed, _, n = oc.chat_json("s", "u", {}, ("ton",), model="m")
    assert n == 2 and parsed["ton"] == 4


def test_post_retries_429_then_ok(monkeypatch):
    fake = _fake_post(
        [_FakeResponse({}, 429), _FakeResponse(_load("ollama_chat_sample.json"))]
    )
    monkeypatch.setattr(oc.requests, "post", fake)
    text = oc.chat([{"role": "user", "content": "x"}], "m")
    assert len(fake.calls) == 2
    assert "Starkes Quartal" in text


def test_post_auth_error_no_retry(monkeypatch):
    fake = _fake_post([_FakeResponse({}, 401)])
    monkeypatch.setattr(oc.requests, "post", fake)
    with pytest.raises(oc.OllamaError) as err:
        oc.chat([{"role": "user", "content": "x"}], "m")
    assert err.value.kind == "auth"
    assert len(fake.calls) == 1


def test_post_network_error_gives_up(monkeypatch):
    fake = _fake_post([real_requests.ConnectionError("down")])
    monkeypatch.setattr(oc.requests, "post", fake)
    with pytest.raises(oc.OllamaError) as err:
        oc.chat([{"role": "user", "content": "x"}], "m")
    assert err.value.kind == "network"
    assert len(fake.calls) == len(oc.RETRY_DELAYS) + 1


def test_missing_key_raises_before_request(monkeypatch):
    monkeypatch.delenv("OLLAMA_API_KEY")
    fake = _fake_post([_FakeResponse({})])
    monkeypatch.setattr(oc.requests, "post", fake)
    with pytest.raises(oc.OllamaError) as err:
        oc.chat([{"role": "user", "content": "x"}], "m")
    assert err.value.kind == "auth"
    assert fake.calls == []


def test_empty_content_is_parse_error(monkeypatch):
    monkeypatch.setattr(
        oc.requests, "post", _fake_post([_FakeResponse({"message": {"content": ""}})])
    )
    with pytest.raises(oc.OllamaError) as err:
        oc.chat([{"role": "user", "content": "x"}], "m")
    assert err.value.kind == "parse"


def test_key_never_logged(monkeypatch, caplog):
    fake = _fake_post([_FakeResponse({}, 500), _FakeResponse({}, 500), _FakeResponse({}, 500), _FakeResponse({}, 500)])
    monkeypatch.setattr(oc.requests, "post", fake)
    with caplog.at_level("DEBUG"):
        with pytest.raises(oc.OllamaError):
            oc.chat([{"role": "user", "content": "x"}], "m")
    assert "ollama-secret" not in caplog.text


def test_base_url_override(monkeypatch):
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434/")
    fake = _fake_post([_FakeResponse(_load("ollama_chat_sample.json"))])
    monkeypatch.setattr(oc.requests, "post", fake)
    oc.chat([{"role": "user", "content": "x"}], "m")
    assert fake.calls[0]["url"] == "http://localhost:11434/api/chat"


def test_list_models_dedupes_and_fails_open(monkeypatch):
    calls = []

    def get(url, headers=None, timeout=None):
        calls.append(url)
        return _FakeResponse(_load("ollama_tags_sample.json"))

    monkeypatch.setattr(oc.requests, "get", get)
    assert oc.list_models() == ["deepseek-v3.1:671b", "gpt-oss:120b"]
    assert calls[0] == "https://ollama.com/api/tags"

    def boom(url, headers=None, timeout=None):
        raise real_requests.ConnectionError("down")

    monkeypatch.setattr(oc.requests, "get", boom)
    assert oc.list_models() == []

    monkeypatch.delenv("OLLAMA_API_KEY")
    assert oc.list_models() == []


def test_chat_tools_parses_tool_calls_and_omits_format(monkeypatch):
    payload = _load("ollama_chat_toolcall_sample.json")
    fake = _fake_post([_FakeResponse(payload)])
    monkeypatch.setattr(oc.requests, "post", fake)
    tools = [{"type": "function", "function": {"name": "company_snapshot", "description": "d", "parameters": {"type": "object"}}}]
    msg = oc.chat_tools([{"role": "user", "content": "x"}], "m", tools=tools, temperature=0.3)
    body = fake.calls[0]["json"]
    assert "format" not in body and body["tools"] == tools and body["options"]["temperature"] == 0.3
    assert msg["content"] == ""
    assert [c["name"] for c in msg["tool_calls"]] == ["company_snapshot", "av_news"]
    assert msg["tool_calls"][0]["arguments"] == {"uid": "SAP"}
    assert msg["tool_calls"][1]["arguments"] == {"uid": "SAP", "days": 7}  # JSON-String geparst
    assert msg["raw_tool_calls"] == payload["message"]["tool_calls"]

    final = _FakeResponse(_load("ollama_chat_final_sample.json"))
    fake2 = _fake_post([final])
    monkeypatch.setattr(oc.requests, "post", fake2)
    msg2 = oc.chat_tools([{"role": "user", "content": "x"}], "m")
    assert "tools" not in fake2.calls[0]["json"]
    assert msg2["tool_calls"] == [] and msg2["content"].startswith("## SAP")

    bad = _FakeResponse({"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "", "arguments": "{bad"}}]}})
    monkeypatch.setattr(oc.requests, "post", _fake_post([bad]))
    with pytest.raises(oc.OllamaError) as err:
        oc.chat_tools([{"role": "user", "content": "x"}], "m")
    assert err.value.kind == "parse"


def test_post_400_carries_error_text_and_tools_unsupported(monkeypatch):
    fake = _fake_post([_FakeResponse({"error": "registry.ollama.ai/library/x does not support tools"}, 400)])
    monkeypatch.setattr(oc.requests, "post", fake)
    with pytest.raises(oc.OllamaError) as err:
        oc.chat_tools([{"role": "user", "content": "x"}], "m", tools=[{"type": "function"}])
    assert "does not support tools" in str(err.value) and err.value.kind == "error"
    assert oc.tools_unsupported(err.value) is True
    assert oc.tools_unsupported(oc.OllamaError("Ollama: HTTP 400 — invalid request")) is False
