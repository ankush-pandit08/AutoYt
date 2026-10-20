import shutil
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from test_youtube_content import item, shorts_draft

from autoyt import ai, youtube, youtube_content
from autoyt.audio import Speech, check_audio


@pytest.mark.parametrize("title", [
    "ChatGPT introduces a new AI model with video capabilities",
    "Meta unveils new wearable glasses",
    "Tesla reveals a flying car prototype",
    "Claude model can now reason over images",
])
def test_accepts_specific_product_and_ai_developments(title):
    assert youtube_content.is_product_development(title)


@pytest.mark.parametrize("title", [
    "How RAM works in your computer", "My coding journey", "AI course roadmap",
    "How To Edit Videos Using Claude Opus (Tutorial)", "AI avatars just made $7.6 million",
    "Best laptop deals", "Learn coding with ChatGPT", "Why memory is different from storage",
])
def test_rejects_evergreen_tips_and_creator_content(title):
    assert not youtube_content.is_product_development(title)


def test_creator_pool_has_no_generic_trending_fallback(cfg, monkeypatch):
    cfg.creator_channel_ids = ["creator-channel"]
    cfg.prefer_tech_insights = True
    client = object()  # No chart or search interface: using either must fail.
    monkeypatch.setattr(youtube, "service", lambda cfg: (client, None))
    monkeypatch.setattr(youtube_content, "followed_creator_items", lambda *_: [
        item(title="Meta unveils new wearable glasses"),
        item("ram123DEF45", title="How RAM works in your computer") | {
            "snippet": {**item()["snippet"], "title": "How RAM works in your computer", "description": "RAM explained"}},
    ])
    topics = youtube_content.discover(cfg)
    assert [t.title for t in topics] == ["Meta unveils new wearable glasses"]


def test_six_beats_and_source_names_are_not_spoken(cfg, script):
    source = youtube_content.SourceBrief(basis="youtube", topic="New AI model", videos=[
        {"url": "https://youtu.be/abc123DEF45", "channel": "Varun Mayya"}])
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    seen = []
    def generate(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        seen.append(data)
        assert "HOOK 0-3s" in instruction
        assert "Do not mention YouTubers" in instruction
        assert "prototypes" in instruction
        value = shorts_draft(script)
        if len(seen) == 1:
            value.narration[0] = "Varun Mayya reveals this new model."
        else:
            assert "Remove creator/channel names" in data["previous_problem"]
        return value
    result = youtube_content.generate(cfg, SimpleNamespace(structured=generate), topic, source)
    assert len(seen) == 2
    assert [(b["start_seconds"], b["end_seconds"]) for b in seen[0]["short_beats"]] == [
        (0, 3), (3, 8), (8, 16), (16, 30), (30, 40), (40, 45)]
    assert [s.target_seconds for s in result.scenes] == [3, 5, 8, 14, 10, 5]
    assert "Varun" not in result.script
    assert result.source_urls == ["https://youtu.be/abc123DEF45"]


def test_scene_word_budget_rejects_equal_length_hook(cfg, script):
    source = youtube_content.SourceBrief(basis="youtube", topic="AI", videos=[{"url": "https://youtu.be/abc123DEF45"}])
    calls = []
    def generate(*args):
        if args[0] is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        value = shorts_draft(script)
        calls.append(True)
        if len(calls) == 1:
            value.narration[0] += " The explanation should be much shorter than this long introduction."
        return value
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    youtube_content.generate(cfg, SimpleNamespace(structured=generate), topic, source)
    assert len(calls) == 2


def test_timed_speech_keeps_subtitle_timeline(cfg, script, tmp_path, monkeypatch):
    cfg.ffmpeg = shutil.which("ffmpeg") or "D:/LoopYt/data/tools/ffmpeg.exe"
    if not Path(cfg.ffmpeg).is_file():
        pytest.skip("FFmpeg unavailable")
    targets = [3, 5, 8, 14, 10, 5]
    script = script.model_copy(update={"scenes": [s.model_copy(update={"target_seconds": t})
                                                for s, t in zip(script.scenes, targets)]})
    def segment(self, text, path):
        seconds = targets[int(path.stem.split('-')[-1])] * .9
        sf.write(path, .1 * np.sin(np.arange(int(seconds * 24000)) * .1), 24000)
    monkeypatch.setattr(Speech, "segment", segment)
    segments = Speech(cfg).synthesize(script, tmp_path)
    assert [s["end"] for s in segments] == pytest.approx([3, 8, 16, 30, 40, 45], abs=.002)
    assert check_audio(tmp_path/'narration.wav')["duration"] == pytest.approx(45, abs=.002)
    from autoyt.video import subtitles
    timings = subtitles(cfg, segments, tmp_path)
    assert timings[-1]["end"] <= segments[-1]["end"] + .001


def test_hinglish_does_not_accept_english_only_narration(cfg, script):
    cfg.narration_language = "hi"
    cfg.script_style = "hinglish"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    with pytest.raises(ValueError, match="Hindi in Devanagari"):
        youtube_content.generate(cfg, SimpleNamespace(structured=lambda *args: shorts_draft(script)),
                                 topic, youtube_content.brief(topic))


def test_unsupported_product_mechanisms_are_rejected(cfg, script):
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    def generate(schema, instruction, data):
        if schema is ai.ScriptAudit:
            assert "A question in a source title is NOT confirmation" in instruction
            return ai.ScriptAudit(unsupported_sentences=["Invented product mechanism"],
                                  misleading=True, defensive_only=True)
        return shorts_draft(script)
    with pytest.raises(ValueError, match="Source metadata does not support"):
        youtube_content.generate(cfg, SimpleNamespace(structured=generate), topic, youtube_content.brief(topic))


def test_timing_mismatch_fails_without_cutting_or_uploading(cfg, script, tmp_path, monkeypatch):
    script = script.model_copy(update={"scenes": [s.model_copy(update={"target_seconds": 3})
                                                for s in script.scenes]})
    def segment(self, text, path):
        sf.write(path, .1 * np.sin(np.arange(2400) * .1), 24000)
    monkeypatch.setattr(Speech, "segment", segment)
    with pytest.raises(ValueError, match="revise its narration length"):
        Speech(cfg).synthesize(script, tmp_path)
    assert check_audio(tmp_path/'voice-00.wav')["duration"] == pytest.approx(.1)
    assert not (tmp_path/'narration.wav').exists()
