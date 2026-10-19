from datetime import datetime, timezone

import pytest

from autoyt import planning, scheduling
from autoyt.models import Topic
from autoyt.pipeline import Pipeline


def test_morning_snapshot_is_ranked_unique_and_generated_only_once(cfg, script, research_data, monkeypatch):
    cfg.max_videos_per_day = 2
    cfg.schedule_times = "06:00,08:00"
    topics = [Topic(title=title, url=f"https://a.example/{i}",
                    published_at=datetime.now(timezone.utc), source="test") for i, title in enumerate([
                        "Quantum processor breakthrough explained", "Quantum processor breakthrough explained",
                        "Browser privacy controls arrive today"])]
    calls = []
    monkeypatch.setattr(planning.discovery, "discover", lambda *args: calls.append("discover") or topics)
    def research(*args):
        result = research_data.model_copy(deep=True)
        for claim in result.claims:
            claim.claim = args[3].title + "."
        return result
    monkeypatch.setattr(planning.research, "research", research)
    def generate(cfg, llm, topic, research):
        calls.append("write")
        return script.model_copy(update={"title": topic.title, "topic": topic.title})
    monkeypatch.setattr(planning.ai, "generate", generate)
    plan = planning.prepare_day(cfg)
    assert plan["state"] == "READY"
    assert [e["slot"] for e in plan["entries"]] == ["06:00", "08:00"]
    assert planning.prepare_day(cfg) == plan
    assert calls == ["discover", "write", "write"]


def test_quota_failure_is_saved_without_repeated_daily_discovery(cfg, monkeypatch):
    topic = Topic(title="Google processor update", url="https://a.example/x",
                  published_at=datetime.now(timezone.utc), source="test")
    calls = []
    monkeypatch.setattr(planning.discovery, "discover", lambda *args: calls.append(1) or [topic])
    def fail(*args):
        raise RuntimeError("TRANSIENT: quota")
    monkeypatch.setattr(planning.research, "research", fail)
    assert planning.prepare_day(cfg)["state"] == "FAILED"
    assert planning.prepare_day(cfg)["entries"] == []
    assert calls == [1]


def test_video_slot_consumes_prepared_script_without_discovery_or_writing(cfg, script, research_data, monkeypatch):
    topic = Topic(title="Google processor update", url="https://a.example/x",
                  published_at=datetime.now(timezone.utc), source="test")
    def forbidden(*args, **kwargs):
        pytest.fail("A video slot must not rediscover topics or generate scripts")
    monkeypatch.setattr(planning.discovery, "discover", forbidden)
    monkeypatch.setattr(planning.ai, "generate", forbidden)
    monkeypatch.setattr(planning.ai, "validate_grounding", lambda *args: None)
    monkeypatch.setattr(planning.research, "independent_sources", lambda sources: sources)
    def stop(self, *args):
        raise ValueError("Stop at voice generation")
    monkeypatch.setattr("autoyt.audio.Speech.synthesize", stop)
    runner = Pipeline(cfg)
    job_id = runner.create_run(dry_run=True, slot="2026-10-18T06:00", prepared={
        "topic": topic.model_dump(mode="json"), "research": research_data.model_dump(mode="json"),
        "script": script.model_dump(mode="json")})
    assert runner.store.get(job_id)["stage"] == 3
    assert runner.store.load(job_id, "script")["script"] == script.script


def test_missing_morning_plan_never_discovers_at_video_slot(cfg, monkeypatch):
    cfg.daily_script_batch = True
    def forbidden(*args, **kwargs):
        pytest.fail("No replacement discovery without a morning script")
    monkeypatch.setattr(Pipeline, "create_run", forbidden)
    assert scheduling.scheduled_run(cfg, "08:00", datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc)) is None
