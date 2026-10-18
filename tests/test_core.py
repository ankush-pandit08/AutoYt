import sqlite3
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import portalocker
import pytest
from pydantic import ValidationError

from autoyt.config import Settings
from autoyt.discovery import duplicate, parse_feed
from autoyt.pipeline import Pipeline, pipeline_lock
from autoyt.scheduling import scheduled_run, slot_key
from autoyt.store import Store


def rss(title="Google AI software update", age=1, volume="", date=True):
    timestamp = format_datetime(datetime.now(timezone.utc)-timedelta(hours=age))
    return f'''<rss version="2.0" xmlns:ht="https://trends.google.com/trending/rss"><channel><title>Technology</title>
        <item><title>{title}</title><link>https://example.com/news</link>
        {f'<pubDate>{timestamp}</pubDate>' if date else ''}
        {f'<ht:approx_traffic>{volume}</ht:approx_traffic>' if volume else ''}</item></channel></rss>'''


def test_valid_rss():
    topics = parse_feed(rss(volume="20,000+"), "test")
    assert topics[0].volume == 20000
    assert 0 <= topics[0].score <= 1


@pytest.mark.parametrize("xml", [rss("Celebrity gossip"), rss(age=25), rss(age=-1), rss(date=False),
                                  rss("Google AI rumor"), "<rss><channel></channel></rss>"])
def test_reject_candidates(xml):
    assert parse_feed(xml, "test") == []


def test_bad_rss():
    with pytest.raises(ValueError):
        parse_feed("broken", "test")


def test_rank():
    assert parse_feed(rss(age=1), "test")[0].score > parse_feed(rss(age=20), "test")[0].score


def test_dedupe():
    assert duplicate("Google launches new AI processor", ["Google launches AI processor"])
    assert not duplicate("Windows software update", ["Apple processor announcement"])


@pytest.mark.parametrize("override", [{"youtube_privacy": "unlisted"}, {"schedule_times": ""},
    {"schedule_times": "08:00,08:00,20:00"}, {"enable_upload": True}, {"video_width": 1920},
    {"ollama_base_url": "https://paid-api.example"}, {"duration_min": 50}])
def test_invalid_configuration(override):
    with pytest.raises((ValidationError, ValueError)):
        Settings(_env_file=None, **override)


def test_job_limits_and_slots(cfg):
    db = Store(cfg.database_path)
    first = db.create(slot="2026-10-18T08:00")
    assert first
    assert db.create(slot="2026-10-18T08:00") is None
    assert db.create(slot="2026-10-18T09:00")
    assert db.create(slot="2026-10-18T10:00")
    assert db.create(slot="2026-10-18T11:00") is None
    assert all(db.create() for _ in range(12))
    assert db.create(dry_run=True)


def test_atomic_topic_reservation(cfg):
    db = Store(cfg.database_path)
    first, second = db.create(), db.create()
    db.reserve_topic(first, "Google AI", "hash")
    db.reserve_topic(first, "Google AI", "hash")
    with pytest.raises(sqlite3.IntegrityError):
        db.reserve_topic(second, "Google AI", "hash")


def test_failed_and_preview_topics_do_not_exhaust_live_discovery(cfg):
    db = Store(cfg.database_path)
    failed = db.create()
    db.reserve_topic(failed, "Google AI", "hash")
    db.update(failed, "FAILED")
    assert "Google AI" not in db.recent_topics(14)
    replacement = db.create()
    db.reserve_topic(replacement, "Google AI", "hash")
    assert "Google AI" in db.recent_topics(14)
    preview = db.create(dry_run=True)
    db.reserve_topic(preview, "Preview only topic", "preview")
    assert "Preview only topic" not in db.recent_topics(14)
    db.update(replacement, "FAILED")
    db.upload_progress(replacement, "INITIATING")
    assert "Google AI" in db.recent_topics(14)
    with pytest.raises(sqlite3.IntegrityError):
        db.reserve_topic(preview, "Google AI", "hash")


def test_private_tests_do_not_block_first_public_story(cfg):
    db = Store(cfg.database_path)
    private = db.create()
    db.reserve_topic(private, "Google AI", "hash")
    db.upload_progress(private, "CONFIRMED", video_id="private-test")
    db.confirm_upload(private, "private-test", "private")
    db.update(private, "AWAITING_REVIEW")
    assert "Google AI" in db.recent_topics(14)
    assert "Google AI" not in db.recent_topics(14, "public")
    public = db.create()
    db.reserve_topic(public, "Google AI", "hash", "public")
    assert "Google AI" in db.recent_topics(14, "public")
    assert db.get(private)["state"] == "AWAITING_REVIEW"


def test_lock(cfg):
    cfg.prepare()
    with pipeline_lock(cfg):
        with pytest.raises(portalocker.exceptions.LockException):
            with pipeline_lock(cfg):
                pass


def test_persisted_checkpoint(cfg):
    db = Store(cfg.database_path)
    job_id = db.create()
    db.update(job_id, "SYNTHESIZING_AUDIO", stage=3)
    db.save(job_id, "script", {"text": "saved"})
    restarted = Store(cfg.database_path)
    assert restarted.get(job_id)["stage"] == 3
    assert restarted.load(job_id, "script") == {"text": "saved"}


def test_dry_run_cannot_resume_live(cfg):
    pipeline = Pipeline(cfg)
    pipeline.store.create(dry_run=False)
    with pytest.raises(ValueError, match="different dry-run"):
        pipeline.create_run(dry_run=True)


def test_ist_and_missed_policy(cfg, monkeypatch):
    moment = datetime(2026, 10, 18, 2, 30, tzinfo=timezone.utc)
    assert slot_key(cfg, moment) == "2026-10-18T08:00"
    calls = []
    monkeypatch.setattr(Pipeline, "create_run", lambda self, **kwargs: calls.append(kwargs))
    scheduled_run(cfg, "08:00", moment)
    assert len(calls) == 1
    scheduled_run(cfg, "08:00", moment+timedelta(minutes=2))
    assert len(calls) == 1
