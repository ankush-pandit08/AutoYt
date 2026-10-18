import pytest

from autoyt.ai import ScriptAudit, ScriptDraft, WriterDraft, assemble_script, generate, writer_script
from autoyt.models import Topic


def test_model_draft_assembles_consistent_narration(script):
    draft = ScriptDraft.model_validate(script.model_dump(exclude={"script", "hook"}))
    result = assemble_script(draft)
    assert result.script == script.script
    assert result.hook == result.scenes[0].narration


def test_short_model_draft_is_rejected_with_measured_count(script):
    data = script.model_dump(exclude={"script", "hook"})
    for scene in data["scenes"]:
        scene["narration"] = "Brief scene narration."
    with pytest.raises(ValueError, match="18 words"):
        assemble_script(ScriptDraft.model_validate(data))


def test_writer_keeps_verified_urls_and_valid_claim_indexes(script, research_data):
    topic = Topic(title=script.topic, url=research_data.sources[0].url,
                  published_at=research_data.sources[0].published_at, source="test")
    draft = WriterDraft(title=script.title, description=script.description,
                        thumbnail_text=script.thumbnail_text,
                        narration=[s.narration for s in script.scenes])
    result = writer_script(draft, topic, research_data)
    assert result.script == script.script
    assert result.source_urls == [s.url for s in research_data.sources]
    assert all(s.claim_ids == [0, 1] for s in result.scenes)
    assert "#Shorts" in result.hashtags


def test_editorial_revision_cannot_bypass_model_audit(cfg, script, research_data, monkeypatch):
    topic = Topic(title=script.topic, url=research_data.sources[0].url,
                  published_at=research_data.sources[0].published_at, source="test")
    draft = WriterDraft(title=script.title, description=script.description,
                        thumbnail_text=script.thumbnail_text, narration=[s.narration for s in script.scenes])
    monkeypatch.setattr("autoyt.ai.validate_grounding", lambda *_: None)

    class RejectingAuditor:
        def structured(self, schema, *_):
            assert schema is ScriptAudit
            return ScriptAudit(unsupported_sentences=["Unsupported statement"], misleading=True,
                               defensive_only=True)

    with pytest.raises(ValueError, match="Reviewed script failed validation"):
        generate(cfg, RejectingAuditor(), topic, research_data, reviewed_draft=draft)


def test_hindi_request_cannot_silently_produce_english(cfg, script, research_data):
    cfg.narration_language = "hi"
    topic = Topic(title=script.topic, url=research_data.sources[0].url,
                  published_at=research_data.sources[0].published_at, source="test")
    draft = WriterDraft(title=script.title, description=script.description,
                        thumbnail_text=script.thumbnail_text, narration=[s.narration for s in script.scenes])
    with pytest.raises(ValueError, match="Hindi narration was requested"):
        generate(cfg, None, topic, research_data, reviewed_draft=draft)
