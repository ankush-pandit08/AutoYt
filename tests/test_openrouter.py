import httpx
import pytest

from autoyt import limits, script_settings
from autoyt.ai import LLM, Audit
from autoyt.openrouter_ai import OpenRouter


def response(status=200, content='{"supported":true,"reasons":[]}', **kwargs):
    return httpx.Response(status, json=kwargs or {"choices": [{
        "finish_reason": "stop", "message": {"content": content}}]})


def setup(cfg, monkeypatch, replies):
    cfg.llm_provider = "openrouter"
    cfg.openrouter_api_key = "private-test-key"
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs))
        return replies.pop(0)
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", lambda _: None)
    return calls


def test_free_request_and_structured_validation(cfg, monkeypatch):
    calls = setup(cfg, monkeypatch, [response(content='{}'), response()])
    assert LLM(cfg).structured(Audit, "Check facts", {"text": "Untrusted source"}).supported
    assert len(calls) == 2
    url, kwargs = calls[0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert "private-test-key" not in url
    body = kwargs["json"]
    assert body["model"].endswith(":free")
    assert body["provider"] == {"require_parameters": True, "allow_fallbacks": False,
                                "max_price": {"prompt": 0, "completion": 0}}
    assert body["response_format"] == {"type": "json_object"}
    assert body["max_tokens"] == 512
    assert "untrusted" in body["messages"][0]["content"]


@pytest.mark.parametrize("status,expected", [(401, "access denied"), (402, "paid fallback"),
    (403, "access denied"), (404, "endpoint"), (503, "endpoint")])
def test_errors_are_bounded_and_sanitized(cfg, monkeypatch, status, expected):
    calls = setup(cfg, monkeypatch, [response(status, error={"message": "private-test-key"})])
    with pytest.raises(RuntimeError, match=expected) as error:
        OpenRouter(cfg).structured(Audit, "test", {})
    assert "private-test-key" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.parametrize("error,expected", [
    ({"message": "daily limit private-test-key"}, "daily free-request allowance"),
    ({"metadata": {"provider_name": "private-test-key"}}, "provider is rate-limited")])
def test_account_and_upstream_limits_are_distinct(cfg, monkeypatch, error, expected):
    calls = setup(cfg, monkeypatch, [response(429, error=error)])
    with pytest.raises(RuntimeError, match=expected) as caught:
        OpenRouter(cfg).structured(Audit, "test", {})
    assert "private-test-key" not in str(caught.value)
    assert len(calls) == 1


def test_missing_key_and_paid_frozen_model_never_call_network(cfg, monkeypatch):
    calls = setup(cfg, monkeypatch, [])
    cfg.openrouter_api_key = ""
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        OpenRouter(cfg).structured(Audit, "test", {})
    cfg.openrouter_api_key = "private-test-key"
    frozen = script_settings.freeze(cfg, {"provider": "openrouter", "model": "paid/model"})
    with pytest.raises(RuntimeError, match="paid routing is disabled"):
        OpenRouter(frozen).structured(Audit, "test", {})
    assert not calls


def test_saved_provider_keeps_voice_and_existing_job_provider(cfg):
    cfg.openrouter_api_key = "private-test-key"
    voice = cfg.tts_voice
    old_job = script_settings.snapshot(cfg)
    updated, saved = script_settings.save(cfg, {"provider": "openrouter"}, 0)
    assert updated.llm_provider == "openrouter"
    assert updated.tts_voice == voice
    assert script_settings.freeze(updated, old_job).llm_provider == old_job["provider"]
    assert "private-test-key" not in str(saved)
