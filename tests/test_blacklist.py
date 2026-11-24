from pathlib import Path
import pytest
from autoyt.podcast.blacklist import is_channel_blocked
from autoyt.podcast.sources import SourceRegistry, PodcastSource
from autoyt.podcast.rights import RightsChecker
from autoyt.store import Store


def test_is_channel_blocked():
    assert is_channel_blocked(source_id="shubhankar_mishra") is True
    assert is_channel_blocked(channel_id="UC0Qb7WEWW3xBop_6LC4t7qQ") is True
    assert is_channel_blocked(channel_url="https://www.youtube.com/@unpluggedshubhankar") is True
    assert is_channel_blocked(channel_name="Unplugged Shubhankar") is True
    assert is_channel_blocked(title="Unplugged Shubhankar full episode") is True
    assert is_channel_blocked(source_id="raj_shamani", channel_name="Raj Shamani") is False


def test_source_registry_blocks_blacklisted_source(tmp_path):
    reg = SourceRegistry(sources_file=tmp_path / "sources.json")
    blocked = PodcastSource(
        id="shubhankar_mishra",
        name="Unplugged Shubhankar",
        show_name="Unplugged",
        youtube_channel_id="UC0Qb7WEWW3xBop_6LC4t7qQ",
        youtube_channel_url="https://www.youtube.com/@unpluggedshubhankar",
        playlist_id=None,
        rss_feed_url=None,
        website_url=None,
        enabled=True,
        priority_topics=["motivation"],
        created_at=None,
    )
    with pytest.raises(ValueError, match="blacklisted"):
        reg.add(blocked)


def test_rights_checker_blocks_blacklisted_episode(tmp_path):
    store = Store(tmp_path / "autoyt.db")
    rc = RightsChecker(store)
    store.save_podcast_episode({
        "id": "test:blocked_ep_1",
        "source_id": "test",
        "video_id": "blocked_ep_1",
        "video_url": "https://youtube.com/watch?v=1",
        "title": "Unplugged Shubhankar Podcast",
        "channel_name": "Unplugged Shubhankar",
        "published_at": "2026-11-24T00:00:00+00:00",
        "duration_seconds": 600,
        "thumbnail_url": "",
        "description": "",
        "discovery_score": 0.5,
        "topics_matched": [],
        "status": "discovered",
        "rights_status": "unknown",
        "rights_basis": None,
        "rights_notes": None,
        "rights_verified_at": None,
        "discovered_at": "2026-11-24T00:00:00+00:00",
        "processed_at": None,
        "error_message": None,
    })

    can_proceed, reason = rc.check_before_processing("test:blocked_ep_1")
    assert can_proceed is False
    assert "blacklisted" in reason.lower()

    with pytest.raises(PermissionError, match="blacklisted"):
        rc.grant_authorization("test:blocked_ep_1", basis="fair_use_claimed")
