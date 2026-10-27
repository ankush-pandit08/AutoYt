import httpx
import pytest

from autoyt import limits, script_settings
from autoyt.ai import LLM, Audit, NaturalEditorialDraft
from autoyt.pipeline import Pipeline


@pytest.mark.parametrize("model", script_settings.LOCAL_MODELS)
def test_local_selection_persists_model_without_key(cfg, monkeypatch, model):
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200,
        json={"models": [{"name": model}]}, request=httpx.Request("GET", url)))
    voice = cfg.tts_voice
    updated, record = script_settings.save(cfg, {"provider": "ollama", "model": model}, 0)
    assert record["selection"] == {"provider": "ollama", "model": model}
    assert updated.ollama_model == model and updated.tts_voice == voice
    assert Pipeline(cfg).cfg.ollama_model == model
    assert script_settings.snapshot(updated) == {"provider": "ollama", "model": model}


def test_local_model_installation_is_checked_before_saving(cfg, monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: httpx.Response(200, json={"models": []}, request=httpx.Request("GET", "http://localhost/api/tags")))
    with pytest.raises(ValueError, match="ollama pull qwen3:8b"):
        script_settings.save(cfg, {"provider": "ollama", "model": "qwen3:8b"}, 0)
    assert script_settings.record(cfg)["version"] == 0
    monkeypatch.setattr(httpx, "get", lambda *a, **kw: (_ for _ in ()).throw(httpx.ConnectError("private data")))
    with pytest.raises(ValueError, match="Start Ollama"):
        script_settings.save(cfg, {"provider": "ollama", "model": "gemma3:4b"}, 0)


@pytest.mark.parametrize("value", [{"provider": "ollama", "model": "unknown:cloud"},
    {"provider": "groq", "model": "gemma3:4b"}])
def test_model_selection_cannot_change_cloud_configuration(value):
    with pytest.raises(ValueError):
        script_settings.ScriptSelection.model_validate(value)


def test_cloud_settings_keep_existing_format(cfg):
    cfg.groq_api_key = "test"
    _, record = script_settings.save(cfg, {"provider": "groq"}, 0)
    assert record["selection"] == {"provider": "groq"}


def test_local_and_cloud_have_separate_time_budgets(cfg):
    cfg.llm_provider = "ollama"
    assert limits.research_duration(cfg) == 1800
    cfg.llm_provider = "groq"
    assert limits.research_duration(cfg) == cfg.research_timeout_seconds


def test_local_writer_has_room_for_narration_and_releases_memory(cfg, monkeypatch):
    cfg.llm_provider, cfg.ollama_model = "ollama", "gemma3:4b"
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs["json"]))
        if url.endswith("/api/generate"):
            return httpx.Response(200, request=httpx.Request("POST", url))
        assert kwargs["json"]["options"]["num_thread"] == 2
        assert kwargs["json"]["options"]["num_gpu"] == 0
        return httpx.Response(200, json={"message": {"content": '{"supported":true,"reasons":[]}' }}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    llm = LLM(cfg)
    assert llm.structured(Audit, "Check supplied facts", {}).supported
    llm.release()
    assert requests[-1][1]["keep_alive"] == 0
    assert requests[-1][1]["model"] == "gemma3:4b"
    # Probe request configuration without mirroring a full generated script fixture.
    monkeypatch.setattr(NaturalEditorialDraft, "model_validate_json", lambda _: "validated")
    assert llm.structured(NaturalEditorialDraft, "Write the short", {}) == "validated"
    assert requests[-1][1]["options"]["num_predict"] == 1100
