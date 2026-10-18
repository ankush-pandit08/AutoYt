import sys
from types import SimpleNamespace

import numpy as np
import pytest

from autoyt.audio import Speech, check_audio, hindi_spoken_text
from autoyt.video import local_fonts
from autoyt.youtube import metadata


def test_hindi_voice_uses_cpu_and_reuses_runtime(cfg, tmp_path, monkeypatch):
    cfg.tts_engine, cfg.narration_language, cfg.tts_voice = "kokoro", "hi", "hm_omega"
    cfg.kokoro_model, cfg.kokoro_voices = tmp_path / "model.onnx", tmp_path / "voices.bin"
    cfg.kokoro_model.touch()
    cfg.kokoro_voices.touch()
    sessions, calls = [], []

    def session(path, sess_options, providers):
        assert providers == ["CPUExecutionProvider"]
        assert sess_options.intra_op_num_threads == 2
        sessions.append(path)
        return object()

    class Runtime:
        @classmethod
        def from_session(cls, *_):
            return cls()

        def create(self, text, **kwargs):
            calls.append(kwargs)
            return .2*np.sin(np.arange(24000)*.05), 24000

    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(SessionOptions=SimpleNamespace,
                                                                 InferenceSession=session))
    monkeypatch.setitem(sys.modules, "kokoro_onnx", SimpleNamespace(Kokoro=Runtime))
    speech = Speech(cfg)
    for name in ("first.wav", "second.wav"):
        speech.segment("नमस्कार, टेक ज्ञान में आपका स्वागत है।", tmp_path / name)
        assert check_audio(tmp_path / name)["duration"] == 1
    assert len(sessions) == 1
    assert all(c["lang"] == "hi" and c["voice"] == "hm_omega" for c in calls)


def test_hindi_does_not_silently_use_an_english_voice(cfg, tmp_path):
    cfg.tts_engine, cfg.narration_language, cfg.tts_voice = "kokoro", "hi", "af_heart"
    with pytest.raises(ValueError, match="Hindi Kokoro voice"):
        Speech(cfg).segment("नमस्कार।", tmp_path / "voice.wav")


def test_hindi_youtube_language_metadata(script):
    translated = script.model_copy(update={"script": "नमस्कार, टेक ज्ञान में आपका स्वागत है।"})
    result = metadata(translated, "test", [{"attribution": "Original graphics"}])
    assert result["snippet"]["defaultLanguage"] == "hi"
    assert result["snippet"]["defaultAudioLanguage"] == "hi"
    assert result["status"]["privacyStatus"] == "private"


def test_hindi_render_cannot_silently_replace_missing_glyphs(cfg, tmp_path):
    cfg.narration_language = "hi"
    with pytest.raises(RuntimeError, match="Hindi font missing"):
        local_fonts(cfg, tmp_path)


def test_hindi_pronunciation_aliases_do_not_change_larger_identifiers():
    original = "npm और WINDOWS; npmjs.com तथा dllhost.exe अलग नाम हैं।"
    spoken = hindi_spoken_text(original)
    assert "एनपीएम और विंडोज़" in spoken
    assert "npmjs.com" in spoken and "dllhost.exe" in spoken
