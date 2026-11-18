"""Comprehensive test suite for the Podcast Clipping Platform."""
from datetime import datetime, timezone

import pytest

from autoyt.podcast.discovery import (
    PodcastEpisode,
    score_episode,
)
from autoyt.podcast.metadata import MetadataGenerator
from autoyt.podcast.rights import RightsChecker, RightsStatus
from autoyt.podcast.sources import PodcastSource, SourceRegistry
from autoyt.podcast.subtitles import SubtitleGenerator, _format_ass_time
from autoyt.podcast.thumbnail import ThumbnailGenerator
from autoyt.podcast.transcription import (
    FullTranscript,
    TranscriptSegment,
    WordTimestamp,
)
from autoyt.store import Store


@pytest.fixture
def temp_store(tmp_path):
    db_file = tmp_path / "test_autoyt.db"
    return Store(db_file)


class TestSourceRegistry:
    def test_builtin_sources(self, tmp_path):
        json_file = tmp_path / "podcast_sources.json"
        reg = SourceRegistry(json_file)
        sources = reg.all()
        assert len(sources) >= 3
        ids = {s.id for s in sources}
        assert "raj_shamani" in ids
        assert "ranveer_allahbadia" in ids
        assert "nikhil_kamath" in ids

    def test_enable_disable_source(self, tmp_path):
        json_file = tmp_path / "podcast_sources.json"
        reg = SourceRegistry(json_file)
        assert reg.get("raj_shamani").enabled is True
        reg.disable("raj_shamani")
        assert reg.get("raj_shamani").enabled is False
        enabled_ids = {s.id for s in reg.enabled()}
        assert "raj_shamani" not in enabled_ids

        # Reload from file to ensure persistence
        reg2 = SourceRegistry(json_file)
        assert reg2.get("raj_shamani").enabled is False


class TestRightsChecker:
    def test_hard_gate_blocks_by_default(self, temp_store):
        checker = RightsChecker(temp_store)
        can_proceed, reason = checker.check_before_processing("raj_shamani:xyz123")
        assert can_proceed is False
        assert "no rights record" in reason.lower()

    def test_grant_authorization(self, temp_store):
        checker = RightsChecker(temp_store)
        record = checker.grant_authorization(
            "raj_shamani:xyz123",
            basis="creator_permission",
            evidence_url="https://example.com/permission",
            notes="Authorized by creator email",
        )
        assert record.status == RightsStatus.AUTHORIZED
        assert record.can_process is True
        assert record.can_publish is True

        can_proceed, msg = checker.check_before_processing("raj_shamani:xyz123")
        assert can_proceed is True
        assert msg == "Authorised"

    def test_block_episode_raises_permission_error(self, temp_store):
        checker = RightsChecker(temp_store)
        checker.block_episode("raj_shamani:blocked_ep", reason="Copyright notice")

        with pytest.raises(PermissionError) as exc_info:
            checker.check_before_processing("raj_shamani:blocked_ep")
        assert "BLOCKED" in str(exc_info.value)


class TestEpisodeDiscoveryScoring:
    def test_scoring_weights(self):
        src = PodcastSource(
            id="test_src",
            name="Test Creator",
            show_name="Test Show",
            youtube_channel_id="UC123",
            youtube_channel_url="https://youtube.com/@test",
            playlist_id=None,
            rss_feed_url=None,
            website_url=None,
            enabled=True,
            priority_topics=["motivation", "startup", "money"],
            created_at=datetime.now(timezone.utc),
        )

        ep = PodcastEpisode(
            id="test_src:vid1",
            source_id="test_src",
            video_id="vid1",
            video_url="https://youtube.com/watch?v=vid1",
            title="How to build a startup and make money with extreme motivation",
            channel_name="Test Creator",
            published_at=datetime.now(timezone.utc),
            duration_seconds=3600,  # 60 mins (optimal)
            thumbnail_url="https://example.com/thumb.jpg",
            description="Discussing business, startup success, and motivation.",
            discovery_score=0.0,
            topics_matched=[],
            already_processed=False,
        )

        score = score_episode(ep, src)
        assert 0.8 <= score <= 1.0
        assert set(ep.topics_matched) == {"motivation", "startup", "money"}


class TestSubtitleGenerator:
    def test_format_ass_time(self):
        assert _format_ass_time(0.0) == "0:00:00.00"
        assert _format_ass_time(65.25) == "0:01:05.25"
        assert _format_ass_time(3661.5) == "1:01:01.50"

    def test_generate_ass_styles(self, tmp_path):
        words = [
            WordTimestamp(word="This", start=10.0, end=10.4, probability=0.99),
            WordTimestamp(word="is", start=10.4, end=10.6, probability=0.99),
            WordTimestamp(word="a", start=10.6, end=10.8, probability=0.99),
            WordTimestamp(word="test.", start=10.8, end=11.2, probability=0.99),
        ]
        sub_gen = SubtitleGenerator(style="dynamic")

        for style in ("minimal", "dynamic", "bold"):
            out_file = tmp_path / f"sub_{style}.ass"
            sub_gen.generate_ass(words, clip_start=10.0, output_file=out_file, style=style)
            assert out_file.is_file()
            content = out_file.read_text(encoding="utf-8")
            assert "[Script Info]" in content
            assert "PlayResX: 1080" in content
            assert "PlayResY: 1920" in content
            assert "Dialogue:" in content


class TestThumbnailGenerator:
    def test_headline_clean(self):
        tg = ThumbnailGenerator()
        cleaned = tg._clean_headline("One Decision Can Change Your Life | Raj Shamani #Shorts")
        assert cleaned == "ONE DECISION CAN CHANGE YOUR LIFE"

    def test_wrap_text(self):
        tg = ThumbnailGenerator()
        lines = tg._wrap_text("ONE DECISION CAN CHANGE YOUR ENTIRE LIFE TODAY", max_chars=18)
        assert len(lines) >= 2


class TestMetadataGenerator:
    def test_generate_metadata(self):
        mg = MetadataGenerator()
        src = PodcastSource(
            id="raj_shamani",
            name="Raj Shamani",
            show_name="Figuring Out",
            youtube_channel_id="UC123",
            youtube_channel_url="https://youtube.com/@raj",
            playlist_id=None,
            rss_feed_url=None,
            website_url=None,
            enabled=True,
            priority_topics=["motivation"],
            created_at=datetime.now(timezone.utc),
        )

        meta = mg.generate(
            clip_title="How to Speak Confidently",
            clip_summary="Public speaking advice from top speakers.",
            source=src,
            episode_title="Episode 588 with Vinh Giang",
            episode_url="https://youtube.com/watch?v=full_ep",
            hashtags=["#Motivation", "#Speaking"],
            rights_basis="creator_permission",
            include_rights_in_desc=True,
        )

        assert meta.title.endswith("#Shorts")
        assert "Raj Shamani" in meta.description
        assert "Figuring Out" in meta.description
        assert "https://youtube.com/watch?v=full_ep" in meta.description
        assert "Authorized via: creator_permission" in meta.description
        assert "#RajShamani" in meta.hashtags

    def test_metadata_enforces_english_and_hinglish_only(self):
        mg = MetadataGenerator()
        src = PodcastSource(
            id="raj_shamani",
            name="Raj Shamani",
            show_name="Figuring Out",
            youtube_channel_id="UC123",
            youtube_channel_url="https://youtube.com/@raj",
            playlist_id=None,
            rss_feed_url=None,
            website_url=None,
            enabled=True,
            priority_topics=["motivation"],
            created_at=datetime.now(timezone.utc),
        )

        # Title with Urdu and Hindi characters
        raw_urdu_hindi_title = "یہ سچ ہے - सफलता का नियम"
        meta = mg.generate(
            clip_title=raw_urdu_hindi_title,
            clip_summary="یہ بہت اچھا کلپ ہے",
            source=src,
            episode_title="Test Episode",
            episode_url="https://youtube.com/watch?v=full_ep",
            hashtags=["#Shorts"],
        )

        # Assert no Urdu (0x0600 - 0x06FF) or Devanagari (0x0900 - 0x097F) characters remain
        for ch in meta.title:
            assert not ('\u0600' <= ch <= '\u06FF'), f"Urdu character found in title: {ch}"
            assert not ('\u0900' <= ch <= '\u097F'), f"Devanagari character found in title: {ch}"

        assert meta.title.endswith("#Shorts")
        assert len(meta.title) > 0

    def test_metadata_engaging_english_description(self):
        mg = MetadataGenerator()
        src = PodcastSource(
            id="raj_shamani",
            name="Raj Shamani",
            show_name="Figuring Out",
            youtube_channel_id="UC123",
            youtube_channel_url="https://youtube.com/@raj",
            playlist_id=None,
            rss_feed_url=None,
            website_url=None,
            enabled=True,
            priority_topics=["motivation"],
            created_at=datetime.now(timezone.utc),
        )

        meta = mg.generate(
            clip_title="🔥 The Brutal Truth About Success",
            clip_summary="In this powerful clip, discover why consistency always beats fleeting motivation.",
            source=src,
            episode_title="Episode 588 with Vinh Giang",
            episode_url="https://youtube.com/watch?v=full_ep",
            hashtags=["#Motivation", "#Success"],
        )

        assert meta.title.startswith("🔥")
        assert meta.title.endswith("#Shorts")
        # Ensure engaging English summary is included at the top
        assert "In this powerful clip, discover why consistency always beats fleeting motivation." in meta.description
        assert "What do you think about this?" in meta.description
        assert "🎙️ Podcast: Figuring Out" in meta.description
        assert "👤 Creator / Guest: Raj Shamani" in meta.description
        assert "🎬 Original Episode: Episode 588 with Vinh Giang" in meta.description
        assert "#Shorts" in meta.hashtags

    def test_clip_finder_english_fallback_titles(self):
        from autoyt.podcast.clip_finder import ClipFinder

        finder = ClipFinder()
        # Test discipline keyword match
        title, summary, hashtags, _ = finder._refine_with_llm_or_heuristic(
            episode_id="ep1",
            text="Consistency and daily discipline are the only things that create results in life.",
            base_score=75.0,
            breakdown={"category": "Motivational"},
        )
        assert "#Shorts" in title
        assert "Discipline" in title
        assert "winners" in summary or "habit" in summary or "discipline" in summary

        # Test money keyword match
        title_m, summary_m, _, _ = finder._refine_with_llm_or_heuristic(
            episode_id="ep2",
            text="How to build real wealth and invest in your business.",
            base_score=80.0,
            breakdown={"category": "Motivational"},
        )
        assert "#Shorts" in title_m
        assert "Money" in title_m
        assert "wealth" in summary_m or "money" in summary_m


class TestLanguageSanitization:
    def test_clean_to_english_hinglish_urdu(self):
        from autoyt.podcast.utils import clean_to_english_hinglish

        urdu_text = "آپ کیسے ہیں یہ بہت اچھا ہے"
        cleaned = clean_to_english_hinglish(urdu_text)
        # Verify no Urdu characters
        assert not any('\u0600' <= ch <= '\u06FF' for ch in cleaned)
        assert "aap" in cleaned or "kise" in cleaned

    def test_clean_to_english_hinglish_hindi(self):
        from autoyt.podcast.utils import clean_to_english_hinglish

        hindi_text = "नमस्ते आप कैसे हैं यह बहुत अच्छा है"
        cleaned = clean_to_english_hinglish(hindi_text)
        # Verify no Devanagari characters
        assert not any('\u0900' <= ch <= '\u097F' for ch in cleaned)
        assert "namaste" in cleaned

    def test_clean_to_english_hinglish_english_preserved(self):
        from autoyt.podcast.utils import clean_to_english_hinglish

        eng_text = "Top 5 Rules for Extreme Focus & Discipline #Shorts"
        cleaned = clean_to_english_hinglish(eng_text)
        assert cleaned == "Top 5 Rules for Extreme Focus & Discipline #Shorts"


class TestStorePodcastTables:
    def test_episodes_and_clips_crud(self, temp_store):
        ep_data = {
            "id": "src1:vid1",
            "source_id": "src1",
            "video_id": "vid1",
            "video_url": "https://youtube.com/watch?v=vid1",
            "title": "Test Title",
            "channel_name": "Test Channel",
            "published_at": "2026-11-17T00:00:00+00:00",
            "duration_seconds": 1800,
            "thumbnail_url": "https://example.com/t.jpg",
            "description": "Test Desc",
            "discovery_score": 0.85,
            "topics_matched": ["motivation"],
            "status": "discovered",
            "rights_status": "unknown",
            "rights_basis": None,
            "rights_notes": None,
            "rights_verified_at": None,
            "discovered_at": "2026-11-17T00:00:00+00:00",
        }
        temp_store.save_podcast_episode(ep_data)
        loaded_ep = temp_store.get_podcast_episode("src1:vid1")
        assert loaded_ep is not None
        assert loaded_ep["title"] == "Test Title"

        temp_store.update_episode_status("src1:vid1", "authorized", rights_status="authorized")
        updated_ep = temp_store.get_podcast_episode("src1:vid1")
        assert updated_ep["status"] == "authorized"
        assert updated_ep["rights_status"] == "authorized"

        # Save clip
        clip_data = {
            "id": "src1:vid1:1",
            "episode_id": "src1:vid1",
            "source_id": "src1",
            "clip_index": 1,
            "start_seconds": 10.0,
            "end_seconds": 65.0,
            "duration_seconds": 55.0,
            "transcript_text": "Motivational speech text",
            "ai_score": 88.5,
            "score_breakdown": {"hook": 22.0, "engagement": 24.0},
            "generated_title": "Great Clip #Shorts",
            "generated_description": "Clip description",
            "hashtags": ["#Motivation", "#Shorts"],
            "status": "candidate",
            "output_path": "d:/output/clip.mp4",
            "thumbnail_path": "d:/output/thumb.png",
        }
        temp_store.save_podcast_clip(clip_data)

        loaded_clip = temp_store.get_podcast_clip("src1:vid1:1")
        assert loaded_clip is not None
        assert loaded_clip["ai_score"] == 88.5
        assert loaded_clip["status"] == "candidate"

        temp_store.update_clip_status("src1:vid1:1", "approved")
        approved_clip = temp_store.get_podcast_clip("src1:vid1:1")
        assert approved_clip["status"] == "approved"


class TestClipFinder:
    def test_clip_window_and_non_overlap(self):
        from autoyt.podcast.clip_finder import ClipFinder

        # Create mock transcript with 12 segments of 10s each (120s total)
        segments = []
        for i in range(12):
            start = i * 10.0
            end = start + 10.0
            text = f"This is statement number {i} discussing motivation and success."
            words = [
                WordTimestamp(word="This", start=start, end=start+1.0, probability=0.9),
                WordTimestamp(word="is", start=start+1.0, end=start+2.0, probability=0.9),
                WordTimestamp(word="motivation.", start=start+2.0, end=end, probability=0.9),
            ]
            segments.append(TranscriptSegment(id=i, start=start, end=end, text=text, words=words))

        transcript = FullTranscript(
            episode_id="test:ep1",
            duration_seconds=120.0,
            language="en",
            segments=segments,
        )

        finder = ClipFinder(min_duration=40.0, max_duration=60.0, target_duration=50.0, min_score=20.0)
        candidates = finder.find_clips(transcript, source_id="test", max_clips=3)

        assert len(candidates) >= 1
        # Check non-overlap
        for i in range(len(candidates)):
            for j in range(i + 1, len(candidates)):
                c1, c2 = candidates[i], candidates[j]
                assert c1.end_seconds <= c2.start_seconds or c2.end_seconds <= c1.start_seconds

        for c in candidates:
            assert 40.0 <= c.duration_seconds <= 60.0
            assert c.ai_score > 0
            assert len(c.suggested_title) > 0


class TestPodcastPublisher:
    def test_unauthorized_episode_blocks_upload(self, temp_store, tmp_path):
        from autoyt.podcast.publisher import PodcastPublisher

        # Setup episode with rights_status='unknown'
        temp_store.save_podcast_episode({
            "id": "src:ep1",
            "source_id": "src",
            "video_id": "ep1",
            "video_url": "https://youtube.com/watch?v=ep1",
            "title": "Unauthorized Episode",
            "channel_name": "Source Channel",
            "published_at": "2026-11-17T00:00:00+00:00",
            "duration_seconds": 1800,
            "status": "discovered",
            "rights_status": "unknown",
            "discovered_at": "2026-11-17T00:00:00+00:00",
        })
        video_file = tmp_path / "clip.mp4"
        video_file.write_bytes(b"dummy video")
        temp_store.save_podcast_clip({
            "id": "src:ep1:1",
            "episode_id": "src:ep1",
            "source_id": "src",
            "clip_index": 1,
            "start_seconds": 0,
            "end_seconds": 50,
            "duration_seconds": 50,
            "transcript_text": "Text",
            "ai_score": 90,
            "status": "approved",
            "output_path": str(video_file),
        })

        publisher = PodcastPublisher(None, temp_store)
        with pytest.raises(PermissionError) as exc_info:
            publisher.upload_clip("src:ep1:1")
        assert "rights layer" in str(exc_info.value).lower()

    def test_unapproved_candidate_clip_blocks_upload(self, temp_store, tmp_path):
        from autoyt.podcast.publisher import PodcastPublisher
        from autoyt.podcast.rights import RightsChecker

        checker = RightsChecker(temp_store)
        temp_store.save_podcast_episode({
            "id": "src:ep2",
            "source_id": "src",
            "video_id": "ep2",
            "video_url": "https://youtube.com/watch?v=ep2",
            "title": "Authorized Episode",
            "channel_name": "Source Channel",
            "published_at": "2026-11-17T00:00:00+00:00",
            "duration_seconds": 1800,
            "status": "authorized",
            "discovered_at": "2026-11-17T00:00:00+00:00",
        })
        checker.grant_authorization("src:ep2", basis="creator_permission")

        video_file = tmp_path / "clip2.mp4"
        video_file.write_bytes(b"dummy video")
        temp_store.save_podcast_clip({
            "id": "src:ep2:1",
            "episode_id": "src:ep2",
            "source_id": "src",
            "clip_index": 1,
            "start_seconds": 0,
            "end_seconds": 50,
            "duration_seconds": 50,
            "transcript_text": "Text",
            "ai_score": 90,
            "status": "candidate",  # NOT approved yet
            "output_path": str(video_file),
        })

        publisher = PodcastPublisher(None, temp_store)
        with pytest.raises(PermissionError) as exc_info:
            publisher.upload_clip("src:ep2:1")
        assert "only approved clips can be uploaded" in str(exc_info.value).lower()

    def test_authorized_and_approved_clip_uploads(self, temp_store, tmp_path, monkeypatch):
        from autoyt.podcast.publisher import PodcastPublisher
        from autoyt.podcast.rights import RightsChecker

        checker = RightsChecker(temp_store)
        temp_store.save_podcast_episode({
            "id": "src:ep3",
            "source_id": "src",
            "video_id": "ep3",
            "video_url": "https://youtube.com/watch?v=ep3",
            "title": "Authorized Episode 3",
            "channel_name": "Source Channel",
            "published_at": "2026-11-17T00:00:00+00:00",
            "duration_seconds": 1800,
            "status": "authorized",
            "discovered_at": "2026-11-17T00:00:00+00:00",
        })
        checker.grant_authorization("src:ep3", basis="creator_permission")

        video_file = tmp_path / "clip3.mp4"
        video_file.write_bytes(b"dummy video")
        thumb_file = tmp_path / "thumb3.png"
        thumb_file.write_bytes(b"dummy thumb")

        temp_store.save_podcast_clip({
            "id": "src:ep3:1",
            "episode_id": "src:ep3",
            "source_id": "src",
            "clip_index": 1,
            "start_seconds": 0,
            "end_seconds": 50,
            "duration_seconds": 50,
            "transcript_text": "Text",
            "ai_score": 95,
            "generated_title": "Inspiring Moment #Shorts",
            "generated_description": "Full attribution here",
            "hashtags": ["#Shorts", "#Motivation"],
            "status": "approved",
            "output_path": str(video_file),
            "thumbnail_path": str(thumb_file),
        })

        # Mock youtube service
        class FakeClient:
            pass

        class FakeCfg:
            pass

        def mock_service(cfg, interactive=False):
            return FakeClient(), {"id": "UCchannel123"}

        def mock_execute_upload(self, client, video_file, title, description, tags, privacy):
            assert privacy == "private"
            assert "Inspiring Moment" in title
            return {"id": "yt_short_mock_999"}

        monkeypatch.setattr("autoyt.podcast.publisher.service", mock_service)
        monkeypatch.setattr(PodcastPublisher, "_execute_upload", mock_execute_upload)
        monkeypatch.setattr(PodcastPublisher, "_upload_thumbnail", lambda self, c, v, p: None)

        publisher = PodcastPublisher(FakeCfg(), temp_store)
        res = publisher.upload_clip("src:ep3:1", privacy="private")

        assert res["video_id"] == "yt_short_mock_999"
        assert "youtube.com/shorts/yt_short_mock_999" in res["youtube_url"]
        assert res["privacy"] == "private"

        # Verify DB status updated to 'uploaded'
        updated_clip = temp_store.get_podcast_clip("src:ep3:1")
        assert updated_clip["status"] == "uploaded"
        assert updated_clip["youtube_video_id"] == "yt_short_mock_999"


class TestPipelineRightsGate:
    def test_pipeline_rejects_unauthorized_episode(self, temp_store):
        from autoyt.podcast.pipeline import PodcastPipeline

        temp_store.save_podcast_episode({
            "id": "src:blocked1",
            "source_id": "src",
            "video_id": "blocked1",
            "video_url": "https://youtube.com/watch?v=blocked1",
            "title": "Unverified Episode",
            "channel_name": "Channel",
            "published_at": "2026-11-17T00:00:00+00:00",
            "duration_seconds": 1800,
            "status": "discovered",
            "rights_status": "unknown",
            "discovered_at": "2026-11-17T00:00:00+00:00",
        })

        pipeline = PodcastPipeline(cfg=None, store=temp_store)
        with pytest.raises(PermissionError) as exc_info:
            pipeline.process_episode("src:blocked1")
        assert "rights gate blocked processing" in str(exc_info.value).lower()


class TestMusicLibrary:
    def test_music_library_vibe_resolution(self):
        from autoyt.podcast.music import MusicLibrary

        lib = MusicLibrary()
        tracks = lib.list_tracks()
        assert len(tracks) >= 3

        # Test vibe resolution
        moti_track = lib.get_track_for_vibe("motivational")
        assert moti_track is not None
        assert moti_track.is_file()

        insp_track = lib.get_track_for_vibe("inspirational")
        assert insp_track is not None
        assert insp_track.is_file()

        serious_track = lib.get_track_for_vibe("psychology")
        assert serious_track is not None
        assert serious_track.is_file()

        comedy_track = lib.get_track_for_vibe("banter")
        assert comedy_track is not None
        assert comedy_track.is_file()

    def test_metadata_generator_music_credit(self):
        from autoyt.podcast.metadata import MetadataGenerator
        from autoyt.podcast.sources import PodcastSource

        source = PodcastSource(
            id="test_src",
            name="Host",
            show_name="The Show",
            youtube_channel_id="ch1",
            youtube_channel_url="https://youtube.com/@test",
            playlist_id=None,
            rss_feed_url=None,
            website_url=None,
            enabled=True,
            priority_topics=["motivation"],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        meta_gen = MetadataGenerator()
        res = meta_gen.generate(
            clip_title="Great Advice",
            clip_summary="Summary",
            source=source,
            episode_title="Ep 1",
            episode_url="https://youtube.com/watch?v=123",
            hashtags=["#Advice"],
            music_credit="Superepic by Alexander Nakarada (CC BY 4.0)",
        )
        assert "Background Music: Superepic by Alexander Nakarada (CC BY 4.0)" in res.description


class TestPodcastScheduler:
    def test_scheduler_jobs_setup(self, temp_store):
        from autoyt.podcast.scheduler import PodcastScheduler

        class FakeCfg:
            pass

        sched = PodcastScheduler(FakeCfg(), temp_store)
        job_ids = {j.id for j in sched.scheduler.get_jobs()}
        assert "podcast_morning_discovery" in job_ids
        assert "podcast_afternoon_processing" in job_ids
        assert "podcast_pre_window_prep" in job_ids
        assert "podcast_publish_slot_1900" in job_ids
        assert "podcast_publish_slot_1930" in job_ids
        assert "podcast_publish_slot_2000" in job_ids
        assert "podcast_publish_slot_2030" in job_ids
        assert "podcast_publish_slot_2100" in job_ids
        assert len(job_ids) == 8



