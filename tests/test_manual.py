import json
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.request import Request, urlopen

import pytest
from pydantic import ValidationError

from autoyt import ai
from autoyt.ai import ScriptAudit
from autoyt.dashboard import Dashboard, make_server
from autoyt.manual import ManualRequest, TopicMatches, find_candidates
from autoyt.models import Topic
from autoyt.pipeline import Pipeline


@pytest.mark.parametrize("value", [
    {"mode": "other", "content": "Some topic"},
    {"mode": "topic", "content": "   "},
    {"mode": "topic", "content": "x" * 181},
    {"mode": "script", "content": "Too short", "topic": "AI tools"},
    {"mode": "topic", "content": "AI tools", "source_urls": ["http://example.com"]},
    {"mode": "topic", "content": "AI tools", "source_urls": ["https://user:password@example.com"]},
])
def test_invalid_manual_input(value):
    with pytest.raises(ValidationError):
        ManualRequest.model_validate(value)


def test_topic_matching_never_accepts_invented_or_unrelated_candidates(cfg, monkeypatch):
    available = [Topic(title=title, url=f"https://example.com/{i}", source="test",
                       published_at=datetime.now(timezone.utc)) for i, title in enumerate([
                           "Google Pixel satellite messaging", "Microsoft Windows security update"])]
    monkeypatch.setattr("autoyt.manual.discovery.discover", lambda *args: available)
    monkeypatch.setattr("autoyt.editorial.evergreen_topics", lambda: [])
    llm = SimpleNamespace(structured=lambda *args: TopicMatches(urls=[available[0].url, "https://fake.example"]))
    selected = find_candidates(cfg, None, llm, ManualRequest(mode="topic", content="Google Pixel satellite"))
    assert selected == [available[0]]


def test_custom_sources_do_not_invent_dates_or_trigger_discovery(cfg, monkeypatch):
    monkeypatch.setattr("autoyt.manual.discovery.discover", lambda *args: pytest.fail("Unexpected discovery"))
    request = ManualRequest(mode="topic", content="How passkeys work", source_urls=[
        "https://fidoalliance.org/passkeys/", "https://developers.google.com/identity/passkeys"])
    selected = find_candidates(cfg, None, None, request)
    assert len(selected) == 1 and selected[0].title == request.topic
    assert selected[0].published_at is None and selected[0].content_kind == "evergreen"


def test_missing_manual_topic_never_falls_back_to_automatic_discovery(cfg, monkeypatch):
    monkeypatch.setattr("autoyt.pipeline.find_candidates", lambda *args: [])
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: pytest.fail("Unrelated discovery"))
    runner = Pipeline(cfg)
    job = runner.create_run(manual_input={"mode": "topic", "content": "My chosen technology"})
    assert runner.store.get(job)["state"] == "SKIPPED"
    assert "No alternative topic" in runner.store.get(job)["error"]


def test_custom_request_does_not_resume_an_unrelated_interrupted_job(cfg, monkeypatch):
    runner = Pipeline(cfg)
    runner.store.create()
    monkeypatch.setattr(runner, "execute", lambda *args: pytest.fail("Wrong job resumed"))
    with pytest.raises(ValueError, match="interrupted"):
        runner.create_run(manual_input={"mode": "topic", "content": "Google Pixel"})


def test_manual_script_survives_failed_job_retry(cfg, script, research_data, monkeypatch):
    monkeypatch.setattr("autoyt.pipeline.research.research", lambda *args: research_data)
    received = []

    def generate(*args, **kwargs):
        received.append(kwargs["provided_narration"])
        raise ValueError("Stop before audio")

    monkeypatch.setattr("autoyt.pipeline.ai.generate", generate)
    runner = Pipeline(cfg)
    request = {"mode": "script", "content": script.script, "topic": script.topic,
               "source_urls": script.source_urls[:2]}
    job = runner.create_run(manual_input=request)
    assert runner.store.get(job)["state"] == "FAILED"
    assert runner.store.get(job)["stage"] == 2
    runner.retry(job)
    assert received == [script.script, script.script]


@pytest.mark.parametrize("changed,unsupported", [(False, False), (True, False), (False, True)])
def test_supplied_narration_is_verbatim_and_audited(cfg, script, research_data, monkeypatch,
                                                  changed, unsupported):
    cfg.llm_provider = "groq"
    narration = [s.narration for s in script.scenes]
    if changed:
        narration[0] = narration[0].replace("Your", "This")
    draft = ai.EditorialDraft(title=script.title, description=script.description,
        thumbnail_text=script.thumbnail_text, narration=narration,
        headlines=[s.headline for s in script.scenes], visual_queries=[s.visual_query for s in script.scenes])
    calls = []

    def structured(schema, instruction, data):
        calls.append(schema)
        if schema is ScriptAudit:
            return ScriptAudit(unsupported_sentences=["Unsupported"] if unsupported else [],
                               misleading=False, defensive_only=True)
        assert data["provided_narration"] == script.script
        return draft

    monkeypatch.setattr(ai, "validate_grounding", lambda *args: None)
    topic = Topic(title=script.topic, url=script.source_urls[0], source="test",
                  published_at=datetime.now(timezone.utc))
    if changed or unsupported:
        with pytest.raises(ValueError, match="rejected after two attempts"):
            ai.generate(cfg, SimpleNamespace(structured=structured), topic, research_data,
                        provided_narration=script.script)
    else:
        result = ai.generate(cfg, SimpleNamespace(structured=structured), topic, research_data,
                             provided_narration=script.script)
        assert result.script == script.script
        assert ScriptAudit in calls


def test_dashboard_forwards_validated_input_without_starting_a_real_pipeline(cfg, monkeypatch):
    app = Dashboard(cfg)
    calls = []
    monkeypatch.setattr(app, "_run", lambda *args: calls.append(args))
    try:
        request = {"mode": "topic", "content": "Google Pixel satellite"}
        app.action("preview", manual_input=request)
        app.future.result(timeout=5)
        assert calls[0][0] == "preview"
        assert calls[0][2]["content"] == request["content"]
    finally:
        app.close()


def test_supplied_script_is_not_rewritten_to_fix_audio_duration(cfg, script, research_data, monkeypatch):
    monkeypatch.setattr("autoyt.pipeline.research.research", lambda *args: research_data)
    calls = []
    monkeypatch.setattr("autoyt.pipeline.ai.generate", lambda *args, **kwargs: calls.append(kwargs) or script)
    monkeypatch.setattr("autoyt.pipeline.audio.Speech.synthesize", lambda *args: [{"end": 70}])
    runner = Pipeline(cfg)
    job = runner.create_run(manual_input={"mode": "script", "content": script.script, "topic": script.topic,
                                         "source_urls": script.source_urls[:2]})
    assert runner.store.get(job)["state"] == "FAILED"
    assert "was not rewritten" in runner.store.get(job)["error"]
    assert len(calls) == 1 and calls[0]["provided_narration"] == script.script


def test_http_accepts_script_payload_and_keeps_original_input(cfg, script, monkeypatch):
    app = Dashboard(cfg)
    received = []
    monkeypatch.setattr(app, "_run", lambda *args: received.append(args))
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    value = {"mode": "script", "content": script.script, "topic": script.topic,
             "source_urls": ["https://example.com/" + str(i) + "x" * 400 for i in range(6)]}
    body = json.dumps({"action": "preview", "manual_input": value}).encode()
    assert len(body) > 2048
    try:
        with urlopen(Request(base + "/api/action", data=body, headers={
                "Origin": base, "X-AutoYT-Token": app.token})) as response:
            assert response.status == 200
        app.future.result(timeout=5)
        assert received[0][0] == "preview" and received[0][2] == value
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


@pytest.mark.parametrize("count", [12, 35, 125, 180])
def test_custom_script_accepts_flexible_length_without_six_sentences(count):
    text = " ".join(["technology"] * count) + "."
    request = ManualRequest(mode="script", topic="Technology explanation", content=text)
    assert request.content == text


@pytest.mark.parametrize("count", [11, 181])
def test_custom_script_rejects_out_of_range_length(count):
    with pytest.raises(ValidationError, match="12"):
        ManualRequest(mode="script", topic="Technology", content=" ".join(["word"] * count))


def test_short_custom_script_reaches_audio_without_discovery(cfg, monkeypatch):
    cfg.content_mode = "youtube_insights"
    text = "This model can explain visible objects and answer spoken questions about the environment around you."
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: pytest.fail("Unexpected discovery"))
    def structured(schema, instruction, data):
        assert schema is ai.NaturalEditorialDraft
        assert data["provided_narration"] == text
        return schema(title="A model explains visible objects", description="A short demonstration of contextual model capabilities.",
            thumbnail_text="Explain visible objects", narration=[text], headlines=["Visible objects"],
            visual_queries=["person using smart glasses"])
    runner = Pipeline(cfg)
    runner.llm = SimpleNamespace(structured=structured, release=lambda: None)
    seen = []
    def audio(self, script, folder):
        seen.append(script)
        raise ValueError("Stopped at audio for isolated test")
    monkeypatch.setattr("autoyt.pipeline.audio.Speech.synthesize", audio)
    job = runner.create_run(dry_run=True, fresh=True, manual_input={"mode":"script", "topic":"Model capabilities", "content":text})
    assert runner.store.get(job)["stage"] == 3
    assert len(seen) == 1 and seen[0].natural_pacing
    assert seen[0].script == text and len(seen[0].scenes) == 1
