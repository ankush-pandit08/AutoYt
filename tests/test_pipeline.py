from datetime import datetime, timezone

import pytest

from autoyt import pipeline as module
from autoyt.models import Topic
from autoyt.pipeline import Pipeline


def test_empty_discovery_skips_without_upload(cfg, monkeypatch):
    monkeypatch.setattr(module.discovery, "discover", lambda *args: [])
    def forbidden(*args):
        pytest.fail("No upload should occur")
    monkeypatch.setattr(module.youtube, "upload", forbidden)
    runner = Pipeline(cfg)
    job_id = runner.create_run()
    assert runner.store.get(job_id)["state"] == "SKIPPED"


def test_failed_stage_resumes_without_rediscovery(cfg, research_data, monkeypatch):
    topic = Topic(title="Google AI processor software update", url="https://a.example/doc",
                  published_at=datetime.now(timezone.utc), source="test")
    calls = []
    monkeypatch.setattr(module.discovery, "discover", lambda *args: calls.append(1) or [topic])
    monkeypatch.setattr(module.research, "research", lambda *args: (_ for _ in ()).throw(ValueError("No evidence")))
    runner = Pipeline(cfg)
    job_id = runner.create_run(dry_run=True)
    assert runner.store.get(job_id)["stage"] == 1
    assert runner.store.get(job_id)["state"] == "FAILED"
    monkeypatch.setattr(module.research, "research", lambda *args: research_data)
    monkeypatch.setattr(module.ai, "generate", lambda *args: (_ for _ in ()).throw(ValueError("Stop at script")))
    Pipeline(cfg).retry(job_id)
    assert len(calls) == 1
    assert runner.store.get(job_id)["stage"] == 2
    assert runner.store.load(job_id, "research")["claims"]


def test_dry_run_full_pipeline_never_uploads(cfg, script, research_data, monkeypatch):
    # Mock only service/media boundaries: this checks real orchestration and checkpoints.
    cfg.enable_upload = True
    cfg.youtube_channel_id = "test-channel"
    topic = Topic(title="Google AI processor update", url="https://a.example/doc",
                  published_at=datetime.now(timezone.utc), source="test")
    monkeypatch.setattr(module.discovery, "discover", lambda *args: [topic])
    monkeypatch.setattr(module.research, "research", lambda *args: research_data)
    monkeypatch.setattr(module.ai, "generate", lambda *args: script)
    monkeypatch.setattr(module.ai, "validate_grounding", lambda *args: None)
    monkeypatch.setattr(module.research, "independent_sources", lambda sources: sources)
    def speech(self, script, folder):
        (folder/"narration.wav").write_bytes(b"audio fixture")
        return [{"text": "test phrase", "start": 0, "end": 45}]
    monkeypatch.setattr(module.audio.Speech, "synthesize", speech)
    monkeypatch.setattr(module.media, "choose_media", lambda *args: [
        {"kind": "graphics", "license": "Original AutoYT graphics", "attribution": "Test original graphic"}])
    monkeypatch.setattr(module.video, "subtitles", lambda *args: [{"start": 0, "end": 45}])
    monkeypatch.setattr(module.video, "render", lambda cfg, script, segments, assets, folder:
                        (folder/"final.mp4").write_bytes(b"video fixture"))
    monkeypatch.setattr(module.video, "quality", lambda *args: {"passed": True})
    def forbidden(*args):
        pytest.fail("Dry-run must never upload")
    monkeypatch.setattr(module.youtube, "upload", forbidden)
    runner = Pipeline(cfg)
    job_id = runner.create_run(dry_run=True)
    assert runner.store.get(job_id)["stage"] == 7
    assert runner.store.get(job_id)["state"] == "READY_TO_UPLOAD"
    assert (cfg.output_dir/job_id/"validation.json").is_file()
