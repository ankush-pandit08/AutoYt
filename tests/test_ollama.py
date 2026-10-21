import httpx
import pytest

from autoyt.ai import LLM, Audit


@pytest.mark.parametrize("status,text,expected", [
    (404, "model missing", "model missing"),
    (500, "out of memory", "insufficient RAM"),
    (503, "busy", "request failed"),
])
def test_ollama_actionable_failures(cfg, monkeypatch, status, text, expected):
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs)
        return httpx.Response(status, text=text, request=httpx.Request("POST", "http://localhost"))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("autoyt.limits.time.sleep", lambda _: None)
    with pytest.raises(RuntimeError, match=expected):
        LLM(cfg).structured(Audit, "test", {})
    assert len(calls) == (3 if status == 503 else 1)
    assert calls[0]["json"]["keep_alive"] == "2m"
    assert calls[0]["json"]["options"]["num_ctx"] == 4096


def test_prompt_retains_instructions_for_user_only_model_templates(cfg, monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", "http://localhost"),
                              json={"message": {"content": '{"supported":true,"reasons":[]}'}})

    monkeypatch.setattr(httpx, "post", post)
    assert LLM(cfg).structured(Audit, "Check exact quotations.", {"article": "external text"}).supported
    prompt = calls[0]["messages"][0]
    assert prompt["role"] == "user"
    assert "Check exact quotations." in prompt["content"]
    assert '<untrusted_data>\n{"article": "external text"}\n</untrusted_data>' in prompt["content"]


def test_hindi_prompt_preserves_unicode_without_escape_token_inflation(cfg, monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, request=httpx.Request("POST", "http://localhost"),
                              json={"message": {"content": '{"supported":true,"reasons":[]}'}})

    monkeypatch.setattr(httpx, "post", post)
    LLM(cfg).structured(Audit, "Audit the translation.", {"narration": "हिंदी में तकनीक समझें।"})
    prompt = calls[0]["messages"][0]["content"]
    assert "हिंदी में तकनीक समझें।" in prompt
    assert r"\u0939" not in prompt


def test_model_is_reused_between_text_calls_and_released_before_media(cfg, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append((url, kwargs["json"]))
        result = {"message": {"content": '{"supported":true,"reasons":[]}'}} if url.endswith("/chat") else {"done": True}
        return httpx.Response(200, request=httpx.Request("POST", url), json=result)
    monkeypatch.setattr(httpx, "post", post)
    model = LLM(cfg)
    model.structured(Audit, "test", {})
    model.release()
    model.release()
    assert len(calls) == 2
    assert calls[0][1]["keep_alive"] == "2m"
    assert calls[1][0].endswith("/api/generate")
    assert calls[1][1]["keep_alive"] == 0


def test_passage_embeddings_are_local_cached_and_unloaded(cfg, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        assert url == cfg.ollama_base_url + "/api/embed"
        return httpx.Response(200, request=httpx.Request("POST", url), json={"embeddings": [[1, 0], [0, 1]]})
    monkeypatch.setattr(httpx, "post", post)
    model = LLM(cfg)
    assert model.embeddings(["First exact passage.", "Second exact passage."]) == [[1, 0], [0, 1]]
    model.embeddings(["First exact passage.", "Second exact passage."])
    assert len(calls) == 1
    assert calls[0]["keep_alive"] == 0
    assert calls[0]["truncate"] is False
