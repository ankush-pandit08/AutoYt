from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from autoyt import limits
from autoyt.ai import LLM, Audit
from autoyt.openrouter_ai import OpenRouter
from autoyt.provider_retry import request, retry_after


def reply(status=200, error=None, headers=None):
    return httpx.Response(status, headers=headers, request=httpx.Request("POST", "https://example.test"),
        json={"error": error} if error else {"choices": [{"finish_reason": "stop", "message": {
            "content": '{"supported":true,"reasons":[]}'}}]})


def test_retry_after_date_nested_headers_and_google_hint():
    date = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=30), usegmt=True)
    assert 28 <= retry_after(reply(429, headers={"retry-after": date})) <= 30
    assert retry_after(reply(429, error={"metadata": {"headers": {"Retry-After": "12"}}})) == 12
    assert retry_after(reply(429, error={"details": [{"retryDelay": "4s"}]})) == 4
    assert retry_after(reply(429, headers={"retry-after": "private-secret"})) is None


def test_missing_hint_recovers_and_does_not_consume_schema_repair(cfg, monkeypatch):
    cfg.llm_provider, cfg.groq_api_key = "groq", "private-secret"
    replies = [reply(429, {"message": "tokens per minute"}) for _ in range(3)] + [reply()]
    waits, calls = [], []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return replies.pop(0)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", waits.append)
    assert LLM(cfg).structured(Audit, "check", {}).supported
    assert waits == [3, 6, 12]
    assert len(calls) == 4
    assert all(c["model"] == cfg.groq_model for c in calls)


def test_openrouter_upstream_without_hint_can_recover(cfg, monkeypatch):
    cfg.openrouter_api_key = "private-secret"
    replies = [reply(429, {"message": "Provider returned error", "metadata": {
        "limit_source": "upstream_provider_shared_pool", "provider_name": "private-secret"}}), reply()]
    waits, calls = [], []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return replies.pop(0)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", waits.append)
    assert OpenRouter(cfg).structured(Audit, "check", {}).supported
    assert waits == [3]
    assert all(c["model"] == cfg.openrouter_model and c["provider"]["max_price"] == {
        "prompt": 0, "completion": 0} for c in calls)


def test_embedded_openrouter_429_uses_same_retry_path(cfg, monkeypatch):
    cfg.openrouter_api_key = "private-secret"
    replies = [reply(200, {"code": 429, "message": "rate limit"}), reply()]
    waits = []
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: replies.pop(0))
    monkeypatch.setattr(limits, "pause", waits.append)
    assert OpenRouter(cfg).structured(Audit, "check", {}).supported
    assert waits == [3]


def test_daily_openrouter_with_upstream_metadata_does_not_retry(cfg, monkeypatch):
    cfg.openrouter_api_key = "private-secret"
    calls = []
    def post(*a, **kw):
        calls.append(1)
        return reply(429, {"message": "free-models-per-day", "metadata": {"provider_name": "private-secret"}},
                     {"retry-after": "1"})
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", lambda _: pytest.fail("daily quota must not retry"))
    with pytest.raises(RuntimeError, match="daily free-request allowance") as e:
        OpenRouter(cfg).structured(Audit, "check", {})
    assert len(calls) == 1 and "private-secret" not in str(e.value)


def test_exhausted_short_quota_stops_after_four_http_attempts(monkeypatch):
    calls, waits = [], []
    def send():
        calls.append(1)
        return reply(429)
    monkeypatch.setattr(limits, "pause", waits.append)
    assert request(send, "Groq", lambda _: ("per minute", None, False)).status_code == 429
    assert len(calls) == 4 and waits == [3, 6, 12]


def test_long_reset_does_not_block_worker(monkeypatch):
    monkeypatch.setattr(limits, "pause", lambda _: pytest.fail("long reset must return"))
    assert request(lambda: reply(429), "Groq", lambda _: ("per minute", 120, False)).status_code == 429


def test_groq_413_is_capacity_error_not_quota(cfg, monkeypatch):
    cfg.groq_api_key = "private-secret"
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: reply(413))
    with pytest.raises(RuntimeError, match="request exceeds.*token capacity"):
        LLM(cfg)._groq_structured(Audit, "check", {})


@pytest.mark.parametrize("daily", [False, True])
def test_gemini_distinguishes_short_limit_from_daily_quota(cfg, monkeypatch, daily):
    cfg.llm_provider, cfg.gemini_api_key = "gemini", "private-secret"
    calls, waits = [], []
    def post(*a, **kw):
        calls.append(1)
        if len(calls) == 1:
            return reply(429, {"message": "quota", "details": [{"retryDelay": "2s"},
                {"quotaId": "GenerateRequestsPerDay" if daily else "GenerateRequestsPerMinute"}]})
        return httpx.Response(200, request=httpx.Request("POST", "https://example.test"), json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"text": '{"supported":true,"reasons":[]}'}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", waits.append)
    if daily:
        with pytest.raises(RuntimeError, match="daily quota"):
            LLM(cfg).structured(Audit, "check", {})
        assert len(calls) == 1 and not waits
    else:
        assert LLM(cfg).structured(Audit, "check", {}).supported
        assert len(calls) == 2 and waits == [3]
