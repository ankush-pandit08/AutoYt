import httpx
import pytest

from autoyt.ai import LLM, Audit


def test_gemini_checks_schema_and_excludes_thoughts(cfg, monkeypatch):
    cfg.llm_provider = "gemini"
    cfg.gemini_api_key = "test-secret"
    calls = []

    def post(url, **kwargs):
        assert url.endswith("/gemini-3.8-flash:generateContent")
        assert "test-secret" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "test-secret"
        calls.append(kwargs["json"])
        text = '{}' if len(calls) == 1 else '{"supported":true,"reasons":[]}'
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "candidates": [{"finishReason": "STOP", "content": {"parts": [
                {"thought": True, "text": "private reasoning"}, {"text": text}]}}]})

    monkeypatch.setattr(httpx, "post", post)
    llm = LLM(cfg)
    assert llm.structured(Audit, "Check facts.", {"text": "यह AI update है।"}).supported
    llm.release()
    assert len(calls) == 2
    assert "यह AI update" in calls[0]["contents"][0]["parts"][0]["text"]
    assert calls[0]["generationConfig"]["responseJsonSchema"] == Audit.model_json_schema()
    assert calls[0]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}


@pytest.mark.parametrize("status,expected", [(400, "rejected"), (403, "access denied"), (429, "quota")])
def test_gemini_errors_do_not_expose_key_or_loop(cfg, monkeypatch, status, expected):
    cfg.llm_provider = "gemini"
    cfg.gemini_api_key = "test-secret"
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(status, text="test-secret", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match=expected) as error:
        LLM(cfg).structured(Audit, "test", {})
    assert "test-secret" not in str(error.value)
    assert len(calls) == 1


def test_gemini_overload_is_transient_and_has_bounded_research_retries(cfg, monkeypatch):
    from autoyt import limits
    cfg.llm_provider = "gemini"
    cfg.gemini_api_key = "test-secret"
    calls = []
    def post(url, **kwargs):
        calls.append(url)
        return httpx.Response(503, text="test-secret", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits.time, "sleep", lambda _: None)
    with limits.research_budget(240), pytest.raises(RuntimeError, match="TRANSIENT: Gemini service unavailable.*503") as caught:
        LLM(cfg).structured(Audit, "test", {})
    assert "test-secret" not in str(caught.value)
    assert len(calls) == 4


@pytest.mark.parametrize("model,level,expected", [
    ("gemini-2.5-flash", "low", None),
    ("gemini-3.5-flash-lite", "minimal", {"thinkingLevel": "minimal"}),
])
def test_thinking_settings_respect_model_family(cfg, monkeypatch, model, level, expected):
    cfg.llm_provider = "gemini"
    cfg.gemini_api_key = "test-secret"
    cfg.gemini_model = model
    cfg.gemini_thinking_level = level
    def post(url, **kwargs):
        assert kwargs["json"]["generationConfig"].get("thinkingConfig") == expected
        return httpx.Response(200, request=httpx.Request("POST", url), json={
            "candidates": [{"finishReason": "STOP", "content": {
                "parts": [{"text": '{"supported":true,"reasons":[]}'}]}}]})
    monkeypatch.setattr(httpx, "post", post)
    assert LLM(cfg).structured(Audit, "test", {}).supported
