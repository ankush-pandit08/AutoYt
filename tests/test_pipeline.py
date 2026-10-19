from datetime import datetime, timezone

import pytest

from autoyt import pipeline as module
from autoyt.models import Topic
from autoyt.pipeline import Pipeline
from autoyt.store import write_json


def test_retry_revalidates_visual_query_only_revision(cfg, script, monkeypatch):
    runner = Pipeline(cfg)
    job_id = runner.store.create()
    runner.store.save(job_id, "script", script.model_dump(mode="json"))
    runner.store.update(job_id, "FAILED", stage=3)
    folder = cfg.output_dir / job_id
    folder.mkdir(parents=True)
    queries = [scene.visual_query for scene in script.scenes]
    queries[0] = "network security hardware"
    write_json(folder / "script_revision.json", {
        "title": script.title, "description": script.description,
        "thumbnail_text": script.thumbnail_text,
        "narration": [s.narration for s in script.scenes],
        "headlines": [s.headline for s in script.scenes], "visual_queries": queries})
    checkpoints = []
    monkeypatch.setattr(runner, "execute", lambda job: checkpoints.append(runner.store.get(job)["stage"]))
    runner.retry(job_id)
    assert checkpoints == [2]


def test_interrupted_job_outside_recent_hundred_is_resumed(cfg, monkeypatch):
    runner = Pipeline(cfg)
    old = runner.store.create()
    with runner.store.connect() as connection:
        connection.execute("UPDATE video_jobs SET created_at='2020-01-01T00:00:00+00:00' WHERE id=?", (old,))
    for _ in range(100):
        job = runner.store.create()
        runner.store.update(job, "SKIPPED")
    resumed = []
    monkeypatch.setattr(runner, "execute", lambda job: resumed.append(job) or job)
    assert runner.create_run() == old
    assert resumed == [old]


def test_empty_discovery_skips_without_upload(cfg, monkeypatch):
    monkeypatch.setattr(module.discovery, "discover", lambda *args: [])
    def forbidden(*args):
        pytest.fail("No upload should occur")
    monkeypatch.setattr(module.youtube, "upload", forbidden)
    runner = Pipeline(cfg)
    job_id = runner.create_run()
    assert runner.store.get(job_id)["state"] == "SKIPPED"


def test_failed_research_refreshes_candidates_before_resuming(cfg, research_data, monkeypatch):
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
    assert len(calls) == 2
    assert runner.store.get(job_id)["stage"] == 2
    assert runner.store.load(job_id, "research")["claims"]


def test_research_tries_next_candidate_without_consuming_another_run(cfg, research_data, monkeypatch):
    candidates = [Topic(title=title, url=f"https://example.com/{index}",
                        published_at=datetime.now(timezone.utc), source="test")
                  for index, title in enumerate(("Google AI processor update", "Microsoft cloud security update"))]
    monkeypatch.setattr(module.discovery, "discover", lambda *args: candidates)
    def research(*args):
        if args[3].url == candidates[0].url:
            raise ValueError("No independent evidence")
        return research_data
    monkeypatch.setattr(module.research, "research", research)
    monkeypatch.setattr(module.ai, "generate", lambda *args: (_ for _ in ()).throw(ValueError("Stop at writer")))
    runner = Pipeline(cfg)
    job_id = runner.create_run()
    assert len(runner.store.jobs()) == 1
    assert runner.store.get(job_id)["topic"] == candidates[1].title
    assert runner.store.get(job_id)["stage"] == 2
    assert runner.store.load(job_id, "research_attempts")[0]["topic"] == candidates[0].title
    assert candidates[0].title not in runner.store.recent_topics(14)


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
