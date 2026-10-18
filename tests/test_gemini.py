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
