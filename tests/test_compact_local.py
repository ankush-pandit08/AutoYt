import json
from types import SimpleNamespace

import pytest

from autoyt import ai, topic_research, youtube_content
from autoyt.local_context import compact_context, output_format, ranked_passages


def test_local_search_keeps_subject_and_needs_no_inference(cfg):
    cfg.llm_provider = "ollama"
    cfg.rss_feeds = []
    topic = SimpleNamespace(title="Anthropic launches European consultancy", youtube_metadata={})
    rss = ('<rss><channel><item><title>Anthropic launches European consultancy</title>'
           '<link>https://www.anthropic.com/news/consultancy</link></item>'
           '<item><title>Dictionary definition</title><link>https://dictionary.example/word</link></item></channel></rss>')
    query, urls = topic_research.source_urls(cfg, SimpleNamespace(get=lambda *a, **kw: rss), None, topic)
    assert query == topic.title
    assert urls == ["https://www.anthropic.com/news/consultancy"]


def test_local_rejects_unreviewed_sites_without_inference(cfg, monkeypatch, research_data):
    cfg.llm_provider = "ollama"
    unknown = research_data.sources[1].model_copy(update={"primary": False, "url": "https://unknown.example/processor"})
    monkeypatch.setattr(topic_research, "source_urls", lambda *a: ("processor instructions", [unknown.url]))
    monkeypatch.setattr(topic_research.research, "extract", lambda *a, **kw: unknown)
    llm = SimpleNamespace(structured=lambda *a: pytest.fail("Unfamiliar sites must not consume inference"))
    with pytest.raises(ValueError, match="No usable web evidence"):
        topic_research.gather(cfg, SimpleNamespace(article=lambda *a: "html"), llm, SimpleNamespace(title="Processor instructions"))


def test_context_targets_late_quotation_and_preserves_nearby_warning(research_data):
    value = research_data.model_copy(deep=True)
    quote = value.claims[0].evidence[0].quote
    value.sources[0].text = "Unrelated navigation text. " * 500 + "WARNING: preliminary vendor claims only. " + quote
    result = compact_context(value)
    assert sum(len(c["text"]) for c in result) <= 3000
    assert quote in result[0]["text"]
    assert "preliminary vendor claims" in result[0]["text"]
    assert result[0]["excerpted"]
    assert quote in value.sources[0].text


def test_local_writer_payload_keeps_full_exact_evidence_without_article_duplicates(research_data):
    full = youtube_content.supporting_evidence(research_data)
    compact = youtube_content.supporting_evidence(research_data, compact=True)
    assert compact["claims"] == full["claims"]
    assert "article_context" not in compact
    assert len(json.dumps(compact)) < len(json.dumps(full))


def test_context_matches_quotes_across_article_whitespace(research_data):
    value = research_data.model_copy(deep=True)
    quote = value.claims[0].evidence[0].quote
    value.sources[0].text = "Navigation. " * 500 + "Important qualification: " + quote.replace(" ", "\n")
    result = compact_context(value)
    assert "Important qualification" in result[0]["text"]
    assert " ".join(quote.split()) in " ".join(result[0]["text"].split())


def test_local_passage_budget_keeps_original_identifiers():
    values = {f"p{i}": (None, "processor details. " * 30) for i in range(20)}
    selected = ranked_passages(values, "processor details")
    assert 2 <= len(selected) <= 6
    assert sum(len(v[1]) for v in selected.values()) <= 2200
    assert all(values[key] == value for key, value in selected.items())


def test_local_json_grammar_bounds_verbose_scene_fields_without_changing_shared_schema():
    original = ai.NaturalEditorialDraft.model_json_schema()
    grammar = output_format(ai.NaturalEditorialDraft)
    assert grammar["properties"]["visual_queries"]["items"]["maxLength"] == 80
    assert grammar["properties"]["narration"]["minItems"] == 3
    assert grammar["properties"]["narration"]["maxItems"] == 3
    assert all(grammar["properties"][name]["minItems"] == grammar["properties"][name]["maxItems"] == 3
               for name in ("narration", "headlines", "visual_queries"))
    assert ai.NaturalEditorialDraft.model_json_schema() == original


def test_compact_writer_and_audit_still_reject_invented_claims(cfg, monkeypatch, research_data, script):
    from test_youtube_content import item, shorts_draft, supported_brief
    cfg.llm_provider = "ollama"
    topic = youtube_content.topics_from_items([item()], cfg)[0]
    seen = []
    def structured(schema, instruction, data):
        seen.append((schema, instruction, data))
        if schema is ai.ScriptAudit:
            assert data["article_context"] and "claims" in data["supporting_evidence"]
            return ai.ScriptAudit(unsupported_sentences=["Invented capability"], misleading=True, defensive_only=True)
        assert "100-140" in instruction
        assert "article_context" not in data["supporting_evidence"]
        return shorts_draft(script)
    with pytest.raises(ValueError, match="do not support"):
        youtube_content.generate(cfg, SimpleNamespace(structured=structured), topic, supported_brief(topic, research_data))
    assert sum(schema is ai.ScriptAudit for schema, _, _ in seen) == 3


def test_production_directions_are_rejected_before_expensive_audit(script):
    from test_youtube_content import shorts_draft
    draft = shorts_draft(script)
    draft.narration[0] = "**Scene 1: Hook** Will robotaxis arrive here?"
    with pytest.raises(ValueError, match="plain speech only"):
        ai.writer_script(draft, SimpleNamespace(title="Robotaxis"))
