import base64
import io

import httpx
import numpy as np
import pytest
import soundfile as sf

from autoyt.audio import Speech, check_audio


def test_gemini_voice_uses_approved_style_caches_and_never_loads_local_voice(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.gemini_api_key = "gemini", "Puck", "test-secret"
    audio = io.BytesIO()
    sf.write(audio, .2 * np.sin(np.arange(24000) * .1), 24000, format="WAV")
    calls = []
    def post(url, **kwargs):
        assert "test-secret" not in url
        assert kwargs["headers"]["x-goog-api-key"] == "test-secret"
        body = kwargs["json"]
        assert body["generation_config"]["speech_config"] == [{"voice": "Puck"}]
        assert body["input"][0]["content"][0]["text"] == "यह AI update है।"
        calls.append(body)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"steps": [
            {"type": "model_output", "content": [{"type": "audio", "data":
                base64.b64encode(audio.getvalue()).decode()}]}]})
    monkeypatch.setattr(httpx, "post", post)
    voice = Speech(cfg)
    voice.segment("यह AI update है।", cfg.cache_dir.parent / "first.wav")
    voice.segment("यह AI update है।", cfg.cache_dir.parent / "second.wav")
    assert len(calls) == 1
    assert check_audio(cfg.cache_dir.parent / "second.wav")["duration"] == 1
    assert voice.pipeline is None


def test_speech_quota_stops_without_silent_local_fallback(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.gemini_api_key = "gemini", "Puck", "test-secret"
    calls = []
    def post(url, **kwargs):
        calls.append(1)
        return httpx.Response(429, text="test-secret", request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match="speech quota") as error:
        Speech(cfg).segment("test", cfg.cache_dir.parent / "voice.wav")
    assert "test-secret" not in str(error.value)
    assert len(calls) == 1


def test_short_speech_rate_limit_waits_then_recovers(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.gemini_api_key = "gemini", "Puck", "test-secret"
    audio = io.BytesIO()
    sf.write(audio, .2 * np.sin(np.arange(24000) * .1), 24000, format="WAV")
    calls, waits = [], []
    def post(url, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(429, request=httpx.Request("POST", url), json={"error": {"details": [
                {"violations": [{"quotaId": "RequestsPerMinute-FreeTier"}]}, {"retryDelay": "2s"}]}})
        return httpx.Response(200, request=httpx.Request("POST", url), json={"steps": [
            {"type": "model_output", "content": [{"type": "audio", "data":
                base64.b64encode(audio.getvalue()).decode()}]}]})
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr("autoyt.gemini_speech.time.sleep", waits.append)
    Speech(cfg).segment("यह AI update है।", cfg.cache_dir.parent / "voice.wav")
    assert len(calls) == 2
    assert waits == [3]


def test_flash_25_uses_generate_content_and_preserves_pcm_chunks(cfg, monkeypatch):
    cfg.tts_engine, cfg.tts_voice, cfg.gemini_api_key = "gemini", "Puck", "test-secret"
    cfg.gemini_tts_model = "gemini-2.5-flash-preview-tts"
    pcm = (6000 * np.sin(np.arange(24000) * .1)).astype("<i2")
    calls = []

    def post(url, **kwargs):
        assert url.endswith("/models/gemini-2.5-flash-preview-tts:generateContent")
        assert kwargs["headers"]["x-goog-api-key"] == "test-secret"
        body = kwargs["json"]
        assert body["generationConfig"]["speechConfig"]["voiceConfig"] == {
            "prebuiltVoiceConfig": {"voiceName": "Puck"}}
        assert body["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert body["contents"][0]["parts"][0]["text"].endswith("यह AI update है।")
        calls.append(body)
        return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{
            "finishReason": "STOP", "content": {"parts": [{"inlineData": {
                "mimeType": "audio/L16;codec=pcm;rate=24000",
                "data": base64.b64encode(chunk.tobytes()).decode()}} for chunk in np.array_split(pcm, 2)]}}]})

    monkeypatch.setattr(httpx, "post", post)
    path = cfg.cache_dir.parent / "flash25.wav"
    Speech(cfg).segment("यह AI update है।", path)
    actual, rate = sf.read(path, dtype="int16")
    assert rate == 24000
    np.testing.assert_array_equal(actual, pcm)
    Speech(cfg).segment("यह AI update है।", path)
    assert len(calls) == 1


@pytest.mark.parametrize("reason,mime,data", [
    ("MAX_TOKENS", "audio/L16;rate=24000", b"\x00\x00"),
    ("STOP", "audio/L16;rate=24000", b"\x00"),
    ("STOP", "audio/mpeg", b"invalid"),
])
def test_flash_25_rejects_incomplete_or_invalid_audio(cfg, monkeypatch, reason, mime, data):
    cfg.tts_engine, cfg.tts_voice, cfg.gemini_api_key = "gemini", "Puck", "test-secret"
    cfg.gemini_tts_model = "gemini-2.5-flash-preview-tts"

    def post(url, **kwargs):
        return httpx.Response(200, request=httpx.Request("POST", url), json={"candidates": [{
            "finishReason": reason, "content": {"parts": [{"inlineData": {
                "mimeType": mime, "data": base64.b64encode(data).decode()}}]}}]})

    monkeypatch.setattr(httpx, "post", post)
    path = cfg.cache_dir.parent / "invalid.wav"
    with pytest.raises(ValueError, match="audio validation"):
        Speech(cfg).segment("यह AI update है।", path)
    assert not path.exists()
