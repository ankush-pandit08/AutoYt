from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from autoyt import ai, discovery, planning, research, youtube, youtube_content
from autoyt.manual import ManualRequest, find_candidates
from autoyt.pipeline import Pipeline


def item(video_id="abc123DEF45", title="Microsoft AI software update", age=2, views=10000):
    return {"id": video_id, "snippet": {"title": title, "description": "An overview of the AI software update.",
            "publishedAt": (datetime.now(timezone.utc)-timedelta(days=age)).isoformat(),
            "channelId": "creator-channel", "channelTitle": "Technology Creator"},
            "statistics": {"viewCount": str(views)}}


def draft(script):
    return ai.EditorialDraft(title=script.title, description=script.description,
        thumbnail_text=script.thumbnail_text, narration=[s.narration for s in script.scenes],
        headlines=[s.headline for s in script.scenes], visual_queries=[s.visual_query for s in script.scenes])


def shorts_draft(script):
    value = draft(script)
    value.narration = [
        "This AI model sees moving objects.",
        "That could change what you ask a wearable device to explain.",
        "The new model processes camera input and spoken questions together, according to the supplied product announcement.",
        "For example, you could point the camera at an unfamiliar device and ask about its controls, instead of typing a detailed description into a separate application first.",
        "That offers a simpler way to get context, but the announcement does not establish accuracy in every environment or situation.",
        "Would you trust this feature for everyday questions around you?",
    ]
    return value


def supported_brief(topic, evidence):
    return youtube_content.brief(topic).model_copy(update={"evidence": evidence,
        "fact_check_status": "source_supported"})


def unchecked_script(script, topic):
    return ai.writer_script(draft(script), topic, source_basis="youtube", source_urls=[topic.url])


def test_discovery_uses_youtube_only_and_caches_api_reads(cfg, monkeypatch):
    cfg.content_mode = "youtube_insights"
    calls = []

    class Client:
        def videos(self):
            return self

        def search(self):
            return self

        def list(self, **kwargs):
            calls.append(kwargs)
            if kwargs.get("type") == "video":
                assert kwargs["order"] == "viewCount" and kwargs["videoCategoryId"] == "28"
                assert "publishedAfter" in kwargs
                data = {"items": [{"id": {"videoId": "abc123DEF45"}}]}
            elif "chart" in kwargs:
                assert kwargs["chart"] == "mostPopular" and kwargs["regionCode"] == "IN"
                data = {"items": []}
            else:
                data = {"items": [item()]}
            return SimpleNamespace(execute=lambda: data)

    monkeypatch.setattr(youtube, "service", lambda cfg: (Client(), None))
    web = SimpleNamespace(get=lambda *args: pytest.fail("Google/RSS retrieval is disabled"))
    first = discovery.discover(cfg, web)
    second = discovery.discover(cfg, web)
    assert len(first) == 1 and first == second and len(calls) == 3
    assert first[0].youtube_metadata["signal"] == "recent_popular"


def test_discovery_rejects_old_nontech_and_own_channel_videos(cfg):
    own = item("own123DEF45")
    own["snippet"]["channelId"] = cfg.youtube_channel_id
    celebrity = item("pop123DEF45", "Celebrity wedding", views=9000000)
    celebrity["snippet"]["description"] = "Wedding celebrations"
    pet_video = item("cat123DEF45", "Why cats rub against your laptop")
    topics = youtube_content.topics_from_items([item(), item("old123DEF45", age=8), own, celebrity, pet_video], cfg)
    assert len(topics) == 1 and topics[0].url.endswith("abc123DEF45")


def test_writer_creates_original_script_with_metadata_consistency_check(cfg, script, research_data):
    cfg.content_mode = "youtube_insights"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    calls = []

    def structured(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        assert schema is ai.NaturalEditorialDraft  # No independent research or claim-selection request.
        assert "ORIGINAL" in instruction and "supporting_evidence" in instruction
        assert data["supporting_evidence"]["claims"]
        assert "source_brief" not in data
        calls.append(schema)
        return shorts_draft(script)

    result = youtube_content.generate(cfg, SimpleNamespace(structured=structured), topic,
                                      supported_brief(topic, research_data))
    assert result.source_basis == "verified" and result.source_urls == [s.url for s in research_data.sources]
    assert all(s.claim_ids for s in result.scenes) and len(calls) == 1


def test_overlong_original_draft_is_supplied_for_bounded_shortening(cfg, script, research_data):
    cfg.content_mode = "youtube_insights"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    long = draft(script)
    long.narration = [line + " Additional context about this technology helps explain the topic more clearly." * 3
                      for line in long.narration]
    calls = []

    def structured(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        calls.append(data)
        if len(calls) == 1:
            return long
        assert data["previous_draft"] == long.model_dump()
        assert "Narration has" in data["previous_problem"]
        assert "SHORTEN" in data["revision_instruction"]
        return shorts_draft(script)

    result = youtube_content.generate(cfg, SimpleNamespace(structured=structured), topic,
                                      supported_brief(topic, research_data))
    assert 60 <= len(result.script.split()) <= 115 and len(calls) == 2


def test_short_complete_draft_is_not_expanded_to_fill_time(cfg, script, research_data):
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    short=shorts_draft(script)
    short.narration=['This technology changes how people use their devices.' for _ in range(6)]
    calls=[]
    def structured(schema,*args):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        calls.append(True)
        return short
    result=youtube_content.generate(cfg,SimpleNamespace(structured=structured),topic,supported_brief(topic,research_data))
    assert len(result.script.split())==48 and len(calls)==1
    assert result.natural_pacing


@pytest.mark.parametrize("bad_quality", [False, True])
def test_pipeline_keeps_article_research_quality_and_upload_protection(cfg, script, research_data, monkeypatch,
                                                                            bad_quality):
    cfg.content_mode = "youtube_insights"
    cfg.enable_upload, cfg.youtube_channel_id = True, "my-channel"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    monkeypatch.setattr(discovery, "discover", lambda *args: [topic])
    monkeypatch.setattr(youtube_content, "research_brief", lambda *args, **kwargs: supported_brief(topic, research_data))
    monkeypatch.setattr(youtube_content, "generate", lambda *args, **kwargs: script)

    def forbidden(*args, **kwargs):
        pytest.fail("No independent verification, article retrieval or actual upload should occur")

    monkeypatch.setattr(research, "research", forbidden)
    monkeypatch.setattr(research, "verify_claims", forbidden)
    monkeypatch.setattr(ai, "validate_grounding", forbidden)
    monkeypatch.setattr(youtube, "upload", forbidden)

    def speech(self, script, folder):
        (folder / "narration.wav").write_bytes(b"audio fixture")
        return [{"start": 0, "end": 45, "text": "Fixture narration"}]

    monkeypatch.setattr("autoyt.audio.Speech.synthesize", speech)
    monkeypatch.setattr("autoyt.media.choose_media", lambda *args: [
        {"kind": "graphics", "license": "Original AutoYT graphics", "attribution": "Original graphics"}])
    monkeypatch.setattr("autoyt.video.subtitles", lambda *args: [])
    monkeypatch.setattr("autoyt.video.render", lambda cfg, script, timing, assets, folder:
                        (folder / "final.mp4").write_bytes(b"video fixture"))
    checked = []

    def quality(*args):
        checked.append(True)
        if bad_quality:
            raise ValueError("Video quality failure")
        return {"passed": True}

    monkeypatch.setattr("autoyt.video.quality", quality)
    runner = Pipeline(cfg)
    job = runner.create_run(dry_run=not bad_quality)
    assert runner.store.get(job)["state"] == ("FAILED" if bad_quality else "READY_TO_UPLOAD")
    assert checked == [True]
    assert runner.store.load(job, "research")["claims"]
    if not bad_quality:
        assert runner.store.load(job, "content_package")["fact_check_status"] == "source_supported"


def test_morning_plan_and_prepared_slot_use_article_research(cfg, script, research_data, monkeypatch):
    cfg.content_mode, cfg.max_videos_per_day, cfg.schedule_times = "youtube_insights", 1, "06:00"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    monkeypatch.setattr(discovery, "discover", lambda *args: [topic])
    monkeypatch.setattr(youtube_content, "research_brief", lambda *args, **kwargs: supported_brief(topic, research_data))
    monkeypatch.setattr(youtube_content, "generate", lambda *args: script)
    monkeypatch.setattr(research, "research", lambda *args: pytest.fail("Research must be off"))
    plan = planning.prepare_day(cfg)
    assert plan["state"] == "READY" and plan["content_mode"] == "youtube_insights"
    entry = plan["entries"][0]
    assert "research" not in entry and entry["source_brief"]["fact_check_status"] == "source_supported"
    runner = Pipeline(cfg)
    monkeypatch.setattr(runner, "execute", lambda job: job)
    job = runner.create_run(prepared=entry, slot="2026-10-20T06:00", dry_run=True)
    assert runner.store.get(job)["stage"] == 3
    assert runner.store.load(job, "source_brief")["basis"] == "youtube"


def test_own_script_needs_no_discovery_or_two_source_gate(cfg, script, monkeypatch):
    cfg.content_mode = "youtube_insights"
    monkeypatch.setattr(discovery, "discover", lambda *args: pytest.fail("Unexpected discovery"))
    request = ManualRequest(mode="script", topic=script.topic, content=script.script)
    topic = find_candidates(cfg, None, None, request)[0]
    source = youtube_content.brief(topic)
    def metadata(schema, instruction, data):
        count = len(data["passages"])
        return schema(title=script.title, description=script.description, thumbnail_text=script.thumbnail_text,
                      headlines=[s.headline for s in script.scenes[:count]],
                      visual_queries=[s.visual_query for s in script.scenes[:count]])
    llm = SimpleNamespace(structured=metadata)
    result = youtube_content.generate(cfg, llm, topic, source, provided_narration=request.content)
    assert result.script == script.script and result.source_basis == "user_input"
    assert result.source_urls == [] and source.fact_check_status == "not_checked"


def test_empty_youtube_topics_skip_without_news_fallback(cfg, monkeypatch):
    cfg.content_mode = "youtube_insights"
    monkeypatch.setattr(discovery, "discover", lambda *args: [])
    monkeypatch.setattr(research, "research", lambda *args: pytest.fail("Unexpected news research"))
    runner = Pipeline(cfg)
    job = runner.create_run()
    assert runner.store.get(job)["state"] == "SKIPPED"
    assert "Google Trends and RSS fallbacks are disabled" in runner.store.get(job)["error"]


def test_old_saved_script_cannot_restart_fact_audits_in_youtube_mode(cfg, monkeypatch):
    cfg.content_mode = "youtube_insights"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    runner = Pipeline(cfg)
    job = runner.store.create()
    runner.store.save(job, "topic", topic.model_dump(mode="json"))
    monkeypatch.setattr(ai, "generate", lambda *args: pytest.fail("Fact-checked generation must not run"))
    with pytest.raises(ValueError, match="previous verified-news workflow"):
        runner._generate_script(job)


@pytest.mark.parametrize("fresh,expected", [(False,"SKIPPED"),(True,"FAILED")])
def test_manual_run_rechecks_research_cooldown_but_automatic_runs_wait(cfg, monkeypatch, fresh, expected):
    cfg.content_mode = "youtube_insights"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    monkeypatch.setattr(discovery, "discover", lambda *args: [topic])
    monkeypatch.setattr(youtube_content, "discover", lambda *args, **kwargs: [topic])
    runner = Pipeline(cfg)
    old = runner.store.create()
    runner.store.update(old,"SKIPPED")
    runner.store.save(old,"research_attempts",[{"topic":topic.title,"reason":"Source temporarily unavailable"}])
    monkeypatch.setattr(youtube_content,"research_brief",lambda *args: (_ for _ in ()).throw(RuntimeError("TRANSIENT: Research reached")))
    job = runner.create_run(fresh=fresh)
    assert runner.store.get(job)["state"] == expected
    assert (runner.store.load(job,"topic") is not None) == fresh


def test_fresh_run_still_excludes_published_creator_topics(cfg, monkeypatch):
    cfg.content_mode = "youtube_insights"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    runner = Pipeline(cfg)
    old = runner.store.create()
    runner.store.reserve_topic(old,topic.title,discovery.fingerprint(topic.title))
    runner.store.update(old,"PUBLISHED")
    monkeypatch.setattr(youtube_content,"discover",lambda *args,**kwargs:[topic])
    job = runner.create_run(fresh=True)
    assert runner.store.get(job)["state"] == "SKIPPED"
    assert "last seven days" in runner.store.get(job)["error"]
