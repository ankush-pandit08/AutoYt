from types import SimpleNamespace

import httpx
import numpy as np
import pytest

from autoyt import elevenlabs_speech as eleven
from autoyt.audio import Speech, check_audio
from autoyt.config import Settings
from autoyt.voice_settings import apply, record, save, selection


def test_default_and_explicit_engine():
    assert Settings(_env_file=None).tts_engine == "gemini"
    assert Settings(_env_file=None, tts_engine="kokoro").tts_engine == "kokoro"
    assert Settings(_env_file=None, tts_engine="kokoro").tts_voice == "am_michael"
    assert Settings(_env_file=None, tts_engine="elevenlabs").tts_voice == ""


def test_fixed_model_pcm_language_and_cache(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.elevenlabs_api_key = "elevenlabs", "voice_from_api", "private-key"
    calls = []

    def post(url, **options):
        assert options["headers"]["xi-api-key"] == "private-key"
        assert options["params"] == {"output_format": "pcm_24000"}
        assert options["json"]["model_id"] == "eleven_flash_v2_5"
        calls.append(options["json"])
        return httpx.Response(200, content=(np.sin(np.arange(24000) * 0.1) * 6000).astype("<i2").tobytes())

    monkeypatch.setattr(httpx, "post", post)
    one = cfg.cache_dir.parent / "one.wav"
    Speech(cfg).segment("Technology explained clearly", one)
    Speech(cfg).segment("Technology explained clearly", one)
    assert len(calls) == 1 and calls[0]["language_code"] == "en"
    assert check_audio(one)["duration"] == 1
    cfg.narration_language, cfg.script_style = "hi", "hinglish"
    Speech(cfg).segment("AI update", one)
    assert "language_code" not in calls[-1]
    cfg.script_style = "standard"
    Speech(cfg).segment("\u0928\u092e\u0938\u094d\u0915\u093e\u0930", one)
    assert calls[-1]["language_code"] == "hi"


@pytest.mark.parametrize(
    "status,code,message",
    [
        (401, "invalid_api_key", "access denied"),
        (401, "missing_permissions", "Voices: Read"),
        (403, "quota_exceeded", "credits exhausted"),
        (429, "too_many_concurrent_requests", "rate limit"),
        (500, "server_error", "temporarily unavailable"),
        (404, "voice_not_found", "unavailable"),
    ],
)
def test_safe_errors_no_fallback_or_retry(cfg, monkeypatch, status, code, message):
    cfg.tts_engine, cfg.tts_voice, cfg.elevenlabs_api_key = "elevenlabs", "api_voice", "private-key"
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        return httpx.Response(status, json={"detail": {"status": code, "message": "private-key"}})

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match=message) as error:
        Speech(cfg).segment("Short sample", cfg.cache_dir.parent / "out.wav")
    assert "private-key" not in str(error.value) and len(calls) == 1
    assert not (cfg.cache_dir.parent / "out.wav").exists()


def test_missing_key(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.elevenlabs_api_key = "elevenlabs", "api_voice", ""
    monkeypatch.setattr(httpx, "post", lambda *a, **k: pytest.fail("Must not call API"))
    with pytest.raises(RuntimeError, match="ELEVENLABS_API_KEY"):
        Speech(cfg).segment("Sample", cfg.cache_dir.parent / "out.wav")


def test_api_voices_pagination_ranking_cache(cfg, monkeypatch):
    cfg.elevenlabs_api_key = "catalog-test-key"
    eleven._catalog.clear()
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        assert url == "https://api.elevenlabs.io/v2/voices"
        return httpx.Response(
            200,
            json={
                "voices": [
                    {
                        "voice_id": "z_api",
                        "name": "Z Voice",
                        "labels": {"accent": "indian", "gender": "male"},
                    },
                    {
                        "voice_id": "a_api",
                        "name": "A Voice",
                        "labels": {"accent": "american", "gender": "female"},
                    },
                ],
                "has_more": True,
                "next_page_token": "next-token",
            },
        )

    monkeypatch.setattr(httpx, "get", get)
    result = eleven.voices(cfg)
    assert result["voices"][0]["id"] == "z_api" and result["next_page"] == "next-token"
    eleven.voices(cfg)
    assert len(calls) == 1
    eleven.voices(cfg, "next-token")
    assert calls[-1]["params"]["next_page_token"] == "next-token"


def test_persistence_language_no_secrets_and_stale_save(cfg):
    cfg.elevenlabs_api_key = "private-key"
    value = {"provider": "elevenlabs", "voice": "voice_from_api", "language": "hinglish"}
    updated, saved = save(cfg, value, 0)
    assert updated.narration_language == "hi" and updated.script_style == "hinglish"
    assert selection(apply(cfg)) == value
    text = (cfg.database_path.parent / "voice-settings.json").read_text()
    assert "private-key" not in text and "api_key" not in text
    with pytest.raises(ValueError, match="Reload"):
        save(cfg, value, 0)
    assert record(cfg)["version"] == 1


def test_pipeline_loads_preferences_without_mutating_existing_run(cfg, monkeypatch):
    from autoyt.pipeline import Pipeline

    cfg.elevenlabs_api_key = "private-key"
    save(cfg, {"provider": "elevenlabs", "voice": "voice_from_api", "language": "hi"}, 0)
    monkeypatch.setattr("autoyt.pipeline.ai.LLM", lambda c: SimpleNamespace())
    runner = Pipeline(cfg)
    assert runner.cfg.tts_engine == "elevenlabs" and runner.cfg.narration_language == "hi"
    assert cfg.tts_engine == "kokoro"
