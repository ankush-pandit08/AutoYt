import httpx
import pytest

from autoyt.ai import LLM, Audit


def test_hosted_audit_validates_schema_and_never_loads_local_model(cfg, monkeypatch):
    cfg.llm_provider = "groq"
    cfg.groq_api_key = "test-secret"
    calls = []

    def post(url, **kwargs):
        assert url == "https://api.groq.com/openai/v1/chat/completions"
        assert kwargs["headers"]["Authorization"] == "Bearer test-secret"
        calls.append(kwargs["json"])
        content = '{}' if len(calls) == 1 else '{"supported":true,"reasons":[]}'
        return httpx.Response(200, request=httpx.Request("POST", url),
                              json={"choices": [{"message": {"content": content}}]})

    monkeypatch.setattr(httpx, "post", post)
    model = LLM(cfg)
    assert model.structured(Audit, "Audit evidence.", {"text": "यह AI update है।"}).supported
    model.release()
    assert len(calls) == 2
    assert "यह AI update" in calls[0]["messages"][1]["content"]
    assert '"field": ["supported"]' in calls[1]["messages"][0]["content"]
    assert '"problem": "missing"' in calls[1]["messages"][0]["content"]


def test_missing_api_key_fails_before_network(cfg, monkeypatch):
    cfg.llm_provider = "groq"
    monkeypatch.setattr(httpx, "post", lambda *a, **kw: pytest.fail("Unexpected network access"))
    with pytest.raises(RuntimeError, match="GROQ_API_KEY"):
        LLM(cfg).structured(Audit, "test", {})


def test_quota_failure_preserves_credentials_and_stops_bounded_retries(cfg, monkeypatch):
    cfg.llm_provider = "groq"
    cfg.groq_api_key = "test-secret"
    def post(url, **kwargs):
        return httpx.Response(429, headers={"retry-after": "3600"}, text="test-secret",
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match="quota") as error:
        LLM(cfg).structured(Audit, "test", {})
    assert "test-secret" not in str(error.value)
