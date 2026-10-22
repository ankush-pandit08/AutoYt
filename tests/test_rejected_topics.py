from datetime import datetime, timedelta, timezone

import pytest

from autoyt import discovery
from autoyt.models import Topic
from autoyt.pipeline import Pipeline
from autoyt.store import Store


def topic(title, url):
    return Topic(title=title, url=url, source="test", published_at=datetime.now(timezone.utc))


@pytest.mark.parametrize("state", ["FAILED", "SKIPPED"])
@pytest.mark.parametrize("preview", [False, True])
def test_history_covers_old_failures_at_any_stage_and_previews(cfg, state, preview):
    store = Store(cfg.database_path)
    old = store.create(dry_run=preview)
    selected = topic("Claude coding agent tutorial", "https://example.com/claude")
    store.save(old, "topic", selected.model_dump(mode="json"))
    store.update(old, state, stage=7, topic="Final video title")
    with store.connect() as db:
        db.execute("UPDATE video_jobs SET updated_at=? WHERE id=?",
                   ((datetime.now(timezone.utc)-timedelta(days=60)).isoformat(), old))
    history = Store(cfg.database_path).rejected_topic_history()
    assert selected.title in history["titles"]
    assert "Final video title" in history["titles"]
    renamed = selected.model_copy(update={"title": "A completely different headline"})
    assert discovery.exclude_rejected([renamed], history) == []
    assert store.rejected_topic_history(old) == {"titles": [], "urls": []}


def test_earlier_rejected_candidates_stay_excluded_even_if_job_succeeds(cfg):
    store = Store(cfg.database_path)
    job = store.create()
    rejected = topic("Claude coding agent tutorial", "https://example.com/claude")
    store.save(job, "research_attempts", [{"topic": rejected.title, "url": rejected.url, "reason": "No evidence"}])
    store.update(job, "PUBLISHED", topic="Google Gemini API update")
    assert discovery.exclude_rejected([rejected], store.rejected_topic_history()) == []


@pytest.mark.parametrize("state", ["FAILED", "SKIPPED"])
@pytest.mark.parametrize("creator_mode,fresh", [(False, False), (False, True), (True, True)])
def test_new_runs_choose_next_topic_while_explicit_retry_can_recheck(cfg, monkeypatch, state, creator_mode, fresh):
    if creator_mode:
        cfg.content_mode = "youtube_insights"
    old = topic("Claude coding agent tutorial", "https://example.com/claude")
    new = topic("Google Gemini multimodal API launch", "https://example.com/gemini")
    runner = Pipeline(cfg)
    failed = runner.store.create()
    runner.store.save(failed, "topic", old.model_dump(mode="json"))
    runner.store.update(failed, state, stage=1, topic=old.title)
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: [old, new])
    monkeypatch.setattr("autoyt.pipeline.youtube_content.discover", lambda *args, **kwargs: [old, new])
    checked = []
    def stop_at_research(cfg, web, llm, selected, *args):
        checked.append(selected.title)
        raise RuntimeError("TRANSIENT: Stop at research for this test")
    monkeypatch.setattr("autoyt.pipeline.research.research", stop_at_research)
    monkeypatch.setattr("autoyt.pipeline.youtube_content.research_brief", stop_at_research)
    job = runner.create_run(fresh=fresh)
    assert runner.store.get(job)["topic"] == new.title
    assert checked == [new.title]
    runner.retry(failed)
    assert checked[-1] == old.title


def test_morning_plan_excludes_rejected_topic(cfg, monkeypatch):
    from autoyt import planning
    cfg.max_videos_per_day = 1
    old = topic("Claude coding agent tutorial", "https://example.com/claude")
    new = topic("Google Gemini multimodal API launch", "https://example.com/gemini")
    store = Store(cfg.database_path)
    failed = store.create()
    store.update(failed, "SKIPPED", topic=old.title)
    monkeypatch.setattr(planning.discovery, "discover", lambda *args: [old, new])
    checked = []
    def reject(cfg, web, llm, selected, *args):
        checked.append(selected.title)
        raise ValueError("No evidence")
    monkeypatch.setattr(planning.research, "research", reject)
    planning.prepare_day(cfg)
    assert checked == [new.title]
