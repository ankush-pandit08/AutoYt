import json
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf

from autoyt import indicf5_speech
from autoyt.audio import Speech


def prepare(cfg):
    cfg.tts_engine = "indicf5"
    cfg.indicf5_python = cfg.cache_dir / "python.exe"
    cfg.cache_dir.mkdir(parents=True)
    cfg.indicf5_python.write_bytes(b"fixture")
    cfg.indicf5_model_dir.mkdir(parents=True)
    (cfg.indicf5_model_dir / "provenance.json").write_text('{"revision":"pinned"}')
    cfg.indicf5_reference_audio.parent.mkdir(parents=True)
    wave = .1 * np.sin(2 * np.pi * 220 * np.arange(48000) / 24000)
    sf.write(cfg.indicf5_reference_audio, wave, 24000)
    cfg.indicf5_reference_text_file.write_text("Reference transcript", encoding="utf-8")


def fake_worker(calls):
    def run(command, **kwargs):
        request = json.loads(open(command[-1], encoding="utf-8").read())
        calls.append(request)
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["timeout"] == 3600
        for item in request["items"]:
            sf.write(item["output"], .1 * np.sin(2 * np.pi * 220 * np.arange(24000) / 24000), 24000)
        return SimpleNamespace(returncode=0)
    return run


def test_local_generation_cache_and_input_identity(cfg, tmp_path, monkeypatch):
    prepare(cfg)
    calls = []
    monkeypatch.setattr(indicf5_speech.subprocess, "run", fake_worker(calls))
    path = tmp_path / "voice.wav"
    Speech(cfg).segment("Hindi and English text", path)
    first = path.read_bytes()
    Speech(cfg).segment("Hindi and English text", path)
    assert len(calls) == 1 and path.read_bytes() == first
    cfg.indicf5_threads = 6
    Speech(cfg).segment("Hindi and English text", path)
    assert len(calls) == 1  # Tuning CPU execution must not discard completed narration.
    cfg.indicf5_steps = 32
    Speech(cfg).segment("Hindi and English text", path)
    assert len(calls) == 2
    assert not list(cfg.cache_dir.glob("indicf5-request-*"))


def test_scenes_share_one_local_worker(cfg, tmp_path, script, monkeypatch):
    prepare(cfg)
    calls = []
    monkeypatch.setattr(indicf5_speech.subprocess, "run", fake_worker(calls))
    timings = Speech(cfg).synthesize(script, tmp_path)
    assert len(calls) == 1 and len(calls[0]["items"]) == 6
    assert len(timings) == 6 and timings[-1]["end"] == 6
    assert (tmp_path / "narration.wav").is_file()


def test_missing_runtime_stops_without_falling_back_to_gemini(cfg, tmp_path):
    cfg.tts_engine = "indicf5"
    with pytest.raises(RuntimeError, match="runtime/model missing"):
        Speech(cfg).segment("Hindi text", tmp_path / "voice.wav")


def test_worker_timeout_preserves_cache_and_cleans_request(cfg, tmp_path, monkeypatch):
    prepare(cfg)
    def timeout(command, **kwargs):
        raise indicf5_speech.subprocess.TimeoutExpired(command, 1800)
    monkeypatch.setattr(indicf5_speech.subprocess, "run", timeout)
    with pytest.raises(RuntimeError, match="timed out"):
        Speech(cfg).segment("Hindi text", tmp_path / "voice.wav")
    assert not list(cfg.cache_dir.glob("indicf5-request-*"))
