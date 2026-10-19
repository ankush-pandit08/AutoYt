from datetime import datetime, timedelta, timezone

import pytest

from autoyt.models import Topic
from autoyt.pipeline import Pipeline
from autoyt.store import Store


def test_research_cooldown_expires_and_does_not_block_failed_voice_jobs(cfg):
    store = Store(cfg.database_path)
    failed = store.create()
    store.save(failed, "research_attempts", [{"topic": "Google Pixel satellite messaging", "reason": "No evidence"}])
    store.update(failed, "FAILED")
    voice = store.create()
    store.update(voice, "FAILED", stage=3, topic="Windows software update")
    assert store.recent_research_rejections() == ["Google Pixel satellite messaging"]
    assert store.recent_topics(14) == []
    with store.connect() as db:
        db.execute("UPDATE video_jobs SET updated_at=? WHERE id=?",
                   ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), failed))
    assert store.recent_research_rejections() == []


def test_new_run_skips_rejected_stories_but_explicit_retry_rechecks(cfg, monkeypatch):
    candidates = [Topic(title=title, url=f"https://example.com/{i}", source="test",
                        published_at=datetime.now(timezone.utc)) for i, title in enumerate([
                            "Google Pixel satellite messaging launches", "Microsoft Windows privacy controls arrive"])]
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: candidates)
    checked = []

    def reject(cfg, web, llm, topic, candidates, folder):
        checked.append(topic.title)
        raise ValueError("Evidence incomplete")

    monkeypatch.setattr("autoyt.pipeline.research.research", reject)
    pipeline = Pipeline(cfg)
    first = pipeline.create_run()
    assert pipeline.store.get(first)["state"] == "FAILED"
    assert pipeline.store.get(first)["topic"] == candidates[-1].title
    assert checked == [t.title for t in candidates]
    second = pipeline.create_run()
    assert pipeline.store.get(second)["state"] == "SKIPPED"
    assert len(checked) == 2
    assert "six hours" in pipeline.store.get(second)["error"]
    pipeline.retry(first)
    assert len(checked) > 2
    assert checked[2] == candidates[-1].title


@pytest.mark.parametrize("age_days,expected_checks", [(3, 1), (6.9, 1), (7.1, 0)])
def test_pipeline_research_uses_seven_day_window(cfg, monkeypatch, age_days, expected_checks):
    candidate = Topic(title="Google Pixel satellite messaging launches", url="https://example.com/pixel",
                      source="test", published_at=datetime.now(timezone.utc)-timedelta(days=age_days))
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: [candidate])
    checked = []

    def reject(*args):
        checked.append(True)
        raise ValueError("Evidence incomplete")

    monkeypatch.setattr("autoyt.pipeline.research.research", reject)
    pipeline = Pipeline(cfg)
    job = pipeline.create_run()
    assert pipeline.store.get(job)["state"] == "FAILED"
    assert len(checked) == expected_checks


def test_retry_refreshes_and_ranks_ai_ahead_of_saved_phone_pool(cfg, monkeypatch):
    cfg.topic_focus = "ai_coding"
    old = [Topic(title=f"Samsung phone update version {i}", url=f"https://example.com/phone/{i}",
                 source="test", score=1, published_at=datetime.now(timezone.utc)) for i in range(45)]
    fresh = Topic(title="Claude introduces agentic coding tools", url="https://example.com/claude",
                  source="test", published_at=datetime.now(timezone.utc), score=.1)
    pipeline = Pipeline(cfg)
    job_id = pipeline.store.create()
    pipeline.store.save(job_id, "candidates", [t.model_dump(mode="json") for t in old])
    pipeline.store.save(job_id, "topic", old[0].model_dump(mode="json"))
    pipeline.store.update(job_id, "FAILED", stage=1, topic=old[0].title)
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: [fresh])
    checked = []

    def reject(cfg, web, llm, topic, candidates, folder):
        checked.append(topic.title)
        raise ValueError("Evidence incomplete")

    monkeypatch.setattr("autoyt.pipeline.research.research", reject)
    pipeline.retry(job_id)
    assert checked[0] == fresh.title
    assert len(checked) == cfg.research_candidate_limit
