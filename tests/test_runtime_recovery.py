import json
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
import soundfile as sf

from autoyt import ai, audio, elevenlabs_speech, limits, pipeline, topic_research


def test_cloud_source_search_rejects_short_dictionary_and_unrelated_results(cfg):
    cfg.llm_provider, cfg.rss_feeds = "groq", []
    rss = ("<rss><channel><item><title>Brown</title><link>https://dictionary.example/brown</link></item>"
           "<item><title>Brown University engineers develop energy efficient AI chips</title>"
           "<link>https://research.example/brown-ai-chips</link></item></channel></rss>")
    topic = SimpleNamespace(title="Brown University engineers AI chips", youtube_metadata={})
    llm = SimpleNamespace(structured=lambda *a: topic_research.SearchQuery(query=topic.title))
    _, urls = topic_research.source_urls(cfg, SimpleNamespace(get=lambda *a, **k: rss), llm, topic)
    assert urls == ["https://research.example/brown-ai-chips"]


def test_voice_quota_restarts_all_segments_with_one_voice_and_persists_retry(cfg, script, tmp_path, monkeypatch):
    cfg.tts_engine, cfg.tts_voice = "elevenlabs", "selected-male"
    calls = []
    monkeypatch.setattr(audio.Speech, "fallback_ready", lambda self: True)

    def segment(self, text, path):
        calls.append((self.cfg.tts_engine, text))
        if self.cfg.tts_engine == "elevenlabs" and len(calls) == 2:
            elevenlabs_speech.checked(httpx.Response(401, json={"detail": {
                "status": "quota_exceeded", "message": "private-provider-response"}}))
        signal = .1 if self.cfg.tts_engine == "kokoro" else .2
        sf.write(path, np.sin(np.arange(2400) * .1) * signal, 24000)

    monkeypatch.setattr(audio.Speech, "segment", segment)
    speech = audio.Speech(cfg)
    segments = speech.synthesize(script, tmp_path)
    assert len(segments) == len(script.scenes)
    assert len([c for c in calls if c[0] == "elevenlabs"]) == 2
    assert len([c for c in calls if c[0] == "kokoro"]) == len(script.scenes)
    samples, _ = sf.read(tmp_path / "voice-00.wav")
    assert np.max(np.abs(samples)) < .11  # Earlier hosted segments were replaced too.
    assert cfg.tts_engine == "elevenlabs" and cfg.tts_voice == "selected-male"
    usage = json.loads((tmp_path / "voice_provider_usage.json").read_text())
    assert usage["active_voice"] == "am_michael" and "private-provider-response" not in usage["reason"]
    calls.clear()
    audio.Speech(cfg).synthesize(script, tmp_path)  # A saved retry stays with the same local voice.
    assert all(c[0] == "kokoro" for c in calls)
    assert len(calls) == len(script.scenes)


@pytest.mark.parametrize("change", [{"tts_fallback_enabled": False}, {"narration_language": "hi"}, {}])
def test_unavailable_or_ineligible_local_voice_preserves_actionable_error(cfg, script, tmp_path, monkeypatch, change):
    cfg.tts_engine = "elevenlabs"
    for key, value in change.items():
        setattr(cfg, key, value)
    monkeypatch.setattr(audio.Speech, "segment", lambda *a: (_ for _ in ()).throw(
        RuntimeError("TRANSIENT: ElevenLabs credits exhausted")))
    with pytest.raises(RuntimeError, match="credits exhausted"):
        audio.Speech(cfg).synthesize(script, tmp_path)
    assert not (tmp_path / "voice_provider_usage.json").exists()


def test_sub_budget_reserves_parent_time_and_never_extends_it(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
    with limits.research_budget(100):
        with pytest.raises(limits.ResearchTimeout):
            with limits.sub_budget(55):
                clock[0] += 60
                limits.check()
        assert limits.remaining() == 40
        with limits.sub_budget(200):
            assert limits.timeout(1000) == 40
    assert not limits.active()


def test_retrieval_timeout_still_audits_already_retrieved_official_facts(cfg, research_data, tmp_path, monkeypatch):
    cfg.research_timeout_seconds = cfg.ollama_research_timeout_seconds = 100
    clock = [100.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
    source = research_data.sources[0].model_copy(update={"title": "processor computer"})
    claims = [c.model_copy(update={"evidence": c.evidence[:1]}) for c in research_data.claims]
    monkeypatch.setattr(topic_research, "source_urls", lambda *a: ("processor computer", [source.url, "https://slow.example/page"]))
    monkeypatch.setattr(topic_research.research, "extract", lambda *a, **k: source)
    monkeypatch.setattr(topic_research.research, "official_links", lambda *a: [])
    monkeypatch.setattr(topic_research, "select_official_claims", lambda *a: claims)

    def article(url):
        if "slow.example" in url:
            clock[0] += 60
            limits.check()
        return "article"

    def audit(*a):
        assert limits.remaining() == 40
        return ai.Audit(supported=True, reasons=[])

    result = topic_research.gather(cfg, SimpleNamespace(article=article), SimpleNamespace(structured=audit),
        SimpleNamespace(title="processor computer"), tmp_path)
    assert result.claims == claims
    assert json.loads((tmp_path / "source_audit.json").read_text())["supported"]
    assert not limits.active()


def test_official_links_are_followed_first_and_retrieval_stops_after_valid_sources(cfg, research_data, monkeypatch):
    cfg.llm_provider = "groq"
    sources = [s.model_copy(update={"title": "processor computer"}) for s in research_data.sources]
    sources[2] = sources[2].model_copy(update={"url": "https://c.example/processor-computer"})
    urls = [s.url for s in sources] + ["https://unneeded.example/page"]
    monkeypatch.setattr(topic_research, "source_urls", lambda *a: ("processor computer", urls))
    monkeypatch.setattr(topic_research.research, "extract", lambda url, *a, **k: next(s for s in sources if s.url == url))
    monkeypatch.setattr(topic_research.research, "official_links", lambda html, url, *a: [sources[2].url] if url == sources[0].url else [])
    monkeypatch.setattr(topic_research, "select_article_claims", lambda *a, **k: research_data.claims)
    visited = []

    def article(url):
        visited.append(url)
        return "article"

    def review(schema, *a):
        return (topic_research.WebSourceReview(credible=True, reason="Technical reporting")
                if schema is topic_research.WebSourceReview else ai.Audit(supported=True, reasons=[]))

    topic_research.gather(cfg, SimpleNamespace(article=article), SimpleNamespace(structured=review), SimpleNamespace(title="processor computer"))
    assert visited == [sources[0].url, sources[2].url, sources[1].url]


def test_pipeline_shows_safe_elevenlabs_cause_instead_of_suppressed_message(cfg, script, monkeypatch):
    def synthesize(*a):
        elevenlabs_speech.checked(httpx.Response(401, json={"detail": {
            "status": "quota_exceeded", "message": "private-provider-response"}}))
    monkeypatch.setattr(audio.Speech, "synthesize", synthesize)
    runner = pipeline.Pipeline(cfg)
    job = runner.store.create()
    runner.store.save(job, "script", script.model_dump(mode="json"))
    runner.store.update(job, "SYNTHESIZING_AUDIO", stage=3)
    runner.execute(job)
    message = runner.store.get(job)["error"]
    assert "credits exhausted" in message and message.startswith("TRANSIENT:")
    assert "suppressed" not in message and "private-provider-response" not in message
