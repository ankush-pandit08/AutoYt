from types import SimpleNamespace

import numpy as np
import pytest
import soundfile as sf
from test_youtube_content import item, shorts_draft, supported_brief

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


def test_six_beats_and_source_names_are_not_spoken(cfg, script, research_data):
    source = youtube_content.SourceBrief(basis="youtube", topic="New AI model", videos=[
        {"url": "https://youtu.be/abc123DEF45", "channel": "Varun Mayya"}])
    source.evidence = research_data
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    seen = []
    def generate(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        seen.append(data)
        assert "Keep normal speaking pace" in instruction
        assert "100-140 words" in instruction
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
    assert [b["beat"] for b in seen[0]["short_beats"]] == [
        "HOOK", "CURIOSITY", "BIG REVEAL", "EXPLANATION", "REAL IMPACT", "ENDING"]
    assert all("start_seconds" not in b for b in seen[0]["short_beats"])
    assert all(s.target_seconds is None for s in result.scenes)
    assert result.natural_pacing
    assert "Varun" not in result.script
    assert result.source_urls == [s.url for s in research_data.sources]


@pytest.mark.parametrize("reveal", [
    "The new AI model processes camera input and answers spoken questions.",
    "The newly announced AI model processes camera input alongside spoken questions, so users can ask about visible objects and receive explanations while using the device.",
])
def test_big_reveal_word_budget_is_advisory(cfg, script, reveal, research_data):
    source = youtube_content.SourceBrief(basis="youtube", topic="AI", videos=[{"url": "https://youtu.be/abc123DEF45"}])
    source.evidence = research_data
    calls = []
    def generate(schema, instruction, data):
        if schema is ai.ScriptAudit:
            return ai.ScriptAudit(unsupported_sentences=[], misleading=False, defensive_only=True)
        assert "NO fixed runtime or per-scene word budget" in instruction
        value = shorts_draft(script)
        value.narration[2] = reveal
        calls.append(True)
        return value
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    result = youtube_content.generate(cfg, SimpleNamespace(structured=generate), topic, source)
    assert len(calls) == 1
    assert result.scenes[2].narration == reveal
    assert result.scenes[2].target_seconds is None


def test_natural_25_second_speech_keeps_subtitle_timeline(cfg, script, tmp_path, monkeypatch):
    legacy_targets = [3, 5, 8, 14, 10, 5]
    durations = [2, 3, 5, 7, 5, 3]
    script = script.model_copy(update={"scenes": [s.model_copy(update={"target_seconds": t})
                                                for s, t in zip(script.scenes, legacy_targets)]})
    raw = []
    def segment(self, text, path):
        seconds = durations[int(path.stem.split('-')[-1])]
        data = .1 * np.sin(np.arange(int(seconds * 24000)) * .1)
        sf.write(path, data, 24000)
        raw.append(sf.read(path,dtype='float32')[0])
    monkeypatch.setattr(Speech, "segment", segment)
    monkeypatch.setattr('autoyt.audio.run',lambda *args,**kwargs:pytest.fail('Voice must not be time-stretched'))
    segments = Speech(cfg).synthesize(script, tmp_path)
    assert [s["end"] for s in segments] == pytest.approx([2,5,10,17,22,25],abs=.002)
    assert check_audio(tmp_path/'narration.wav')["duration"] == pytest.approx(25, abs=.002)
    combined,rate=sf.read(tmp_path/'narration.wav',dtype='float32')
    assert rate==24000 and np.array_equal(combined,np.concatenate(raw))
    from autoyt.video import subtitles
    timings = subtitles(cfg, segments, tmp_path)
    assert timings[-1]["end"] <= 25.001


def test_hinglish_does_not_accept_english_only_narration(cfg, script, research_data):
    cfg.narration_language = "hi"
    cfg.script_style = "hinglish"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    with pytest.raises(ValueError, match="Hindi in Devanagari"):
        youtube_content.generate(cfg, SimpleNamespace(structured=lambda *args: shorts_draft(script)),
                                 topic, supported_brief(topic, research_data))


def test_unsupported_product_mechanisms_are_rejected(cfg, script, research_data):
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    def generate(schema, instruction, data):
        if schema is ai.ScriptAudit:
            assert "A question in a source title is NOT confirmation" in instruction
            return ai.ScriptAudit(unsupported_sentences=["Invented product mechanism"],
                                  misleading=True, defensive_only=True)
        return shorts_draft(script)
    with pytest.raises(ValueError, match="Retrieved articles do not support"):
        youtube_content.generate(cfg, SimpleNamespace(structured=generate), topic, supported_brief(topic, research_data))


def test_old_beat_targets_do_not_pad_or_reject_natural_audio(cfg, script, tmp_path, monkeypatch):
    script = script.model_copy(update={"scenes": [s.model_copy(update={"target_seconds": 3})
                                                for s in script.scenes]})
    def segment(self, text, path):
        sf.write(path, .1 * np.sin(np.arange(2400) * .1), 24000)
    monkeypatch.setattr(Speech, "segment", segment)
    segments=Speech(cfg).synthesize(script,tmp_path)
    assert segments[-1]['end']==pytest.approx(.6)
    assert check_audio(tmp_path/'narration.wav')["duration"]==pytest.approx(.6)


def test_natural_story_can_use_three_scenes_and_more_detail(cfg,script,research_data):
    value=shorts_draft(script)
    # Combine related beats while retaining every word of the grounded draft.
    value=ai.NaturalEditorialDraft(title=value.title,description=value.description,thumbnail_text=value.thumbnail_text,
        narration=[' '.join(value.narration[i:i+2]) for i in (0,2,4)],
        headlines=value.headlines[:3],visual_queries=value.visual_queries[:3])
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    def response(schema,*args):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        return value
    result=youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,supported_brief(topic,research_data))
    assert len(result.scenes)==3 and result.natural_pacing
    assert result.script==' '.join(value.narration)


def test_natural_story_allows_richer_script_beyond_old_word_cap(cfg,script,research_data):
    value=shorts_draft(script)
    value.narration[3]+=' Clear context helps explain the supported product capability and the practical limitations described in the announcement, while separating company claims from demonstrated results for everyday users.'
    assert 115 < len(' '.join(value.narration).split()) <= 180
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    def response(schema,*args):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        return value
    result=youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,supported_brief(topic,research_data))
    assert len(result.script.split())>115 and result.natural_pacing


def test_single_scene_story_uses_a_short_opening_hook(cfg,script,research_data):
    value=shorts_draft(script)
    text=('The new model can answer questions about camera input and the visible environment. '
          'It combines spoken questions with images so users can ask about the controls on an unfamiliar device '
          'and receive a contextual explanation.')
    assert len(text)>200
    value=ai.NaturalEditorialDraft(title=value.title,description=value.description,thumbnail_text=value.thumbnail_text,
        narration=[text],headlines=value.headlines[:1],visual_queries=value.visual_queries[:1])
    topic=youtube_content.topics_from_items([item()],cfg)[0]
    def response(schema,*args):
        if schema is ai.ScriptAudit:
            return schema(unsupported_sentences=[],misleading=False,defensive_only=True)
        return value
    result=youtube_content.generate(cfg,SimpleNamespace(structured=response),topic,supported_brief(topic,research_data))
    assert len(result.scenes)==1 and len(result.hook)<=200
    assert result.script.startswith(result.hook)
