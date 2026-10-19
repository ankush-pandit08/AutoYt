from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from autoyt.config import Settings
from autoyt.discovery import rank_insights
from autoyt.editorial import category, content_package, evergreen_topics
from autoyt.models import Topic
from autoyt.pipeline import Pipeline
from autoyt.research import extract, publication_hint, select_claims


def test_channel_schedule_defaults():
    cfg = Settings(_env_file=None)
    assert cfg.schedule_times == "08:00,14:00,20:00"
    assert cfg.max_videos_per_day == 3


def test_evergreen_documents_do_not_invent_publication_dates(cfg, monkeypatch):
    doc = SimpleNamespace(title="Passkeys explained", date=None, text="Passkeys protect sign-ins. "*30)
    monkeypatch.setattr("autoyt.research.trafilatura.bare_extraction", lambda *args, **kwargs: doc)
    with pytest.raises(ValueError, match="Undated"):
        extract("https://fidoalliance.org/passkeys/", "html", cfg)
    source = extract("https://fidoalliance.org/passkeys/", "html", cfg, evergreen=True)
    assert source.published_at is None
    assert source.date_basis == "current_document_no_publication_date"
    assert source.retrieved_at
    doc.date = (datetime.now(timezone.utc)-timedelta(days=365)).isoformat()
    assert extract("https://fidoalliance.org/passkeys/", "html", cfg, evergreen=True).published_at.year
    with pytest.raises(ValueError, match="Stale"):
        extract("https://fidoalliance.org/passkeys/", "html", cfg)


def test_two_independent_official_documents_can_corroborate_evergreen(research_data):
    research_data.sources[1].primary = True
    second = "Memory holds data used by running programs."
    for source in research_data.sources[:2]:
        source.text += " " + second

    class Model:
        def structured(self, schema, instruction, data):
            return schema(claim_ids=["c0", "c1"])

    assert len(select_claims(Model(), research_data.sources[:2], include_primary=True)) == 2


def test_slot_preferences_and_evergreen_last():
    now = datetime.now(timezone.utc)
    ai = Topic(title="Claude AI coding release", url="https://example.com/ai", source="test", published_at=now, score=.8)
    phone = Topic(title="Samsung smartphone software release", url="https://example.com/phone", source="test", published_at=now, score=.8)
    evergreen = evergreen_topics()[0]
    assert publication_hint(evergreen) is None
    assert rank_insights([evergreen, phone, ai], focus="ai_coding", slot="08:00") == [ai, phone, evergreen]
    assert rank_insights([evergreen, phone, ai], focus="ai_coding", slot="14:00") == [phone, ai, evergreen]


def test_news_budget_does_not_exclude_evergreen_fallback(cfg, research_data, monkeypatch):
    cfg.research_candidate_limit = 12
    news = [Topic(title=f"Google release {i}", url=f"https://example.com/{i}", source="test",
                  published_at=datetime.now(timezone.utc)) for i in range(15)]
    evergreen = evergreen_topics()[0]
    monkeypatch.setattr("autoyt.pipeline.discovery.discover", lambda *args: news+[evergreen])
    checked = []

    def research(cfg, web, llm, topic, candidates, folder):
        checked.append(topic)
        if topic.content_kind == "news":
            raise ValueError("Unverified news")
        return research_data

    monkeypatch.setattr("autoyt.pipeline.research.research", research)
    monkeypatch.setattr("autoyt.pipeline.ai.generate", lambda *args: (_ for _ in ()).throw(ValueError("Stop after research")))
    runner = Pipeline(cfg)
    job = runner.create_run(dry_run=True)
    assert len(checked) == 13 and checked[-1] == evergreen
    assert runner.store.get(job)["stage"] == 2
    assert runner.store.load(job, "editorial")["content_kind"] == "evergreen"


def test_complete_content_package(script, research_data, tmp_path):
    topic = evergreen_topics()[0]
    package = content_package(topic, research_data, script, [{"start": 0, "end": 3, "text": "Caption"}], [], tmp_path)
    assert package["fact_check_status"] == "verified"
    assert package["script"] == package["voiceover_text"] == script.script
    assert len(package["scenes"]) == 6 and package["timestamped_subtitles"]
    assert package["sources"][0]["published_at"]
    assert package["thumbnail_concept"] and package["thumbnail_file"]
    assert category("Claude coding assistant") == "Artificial Intelligence & LLMs"
    assert category("Passkeys prevent phishing") == "Cybersecurity & Digital Awareness"


def test_ai_hashtags_and_visual_keywords_follow_scene_queries(script, research_data):
    from autoyt.ai import EditorialDraft, writer_script
    topic = evergreen_topics()[0]
    queries = ["robot hand", "coding laptop", "neural network", "computer server", "developer desk", "data center"]
    draft = EditorialDraft(title=script.title, description=script.description,
        thumbnail_text=script.thumbnail_text, narration=[s.narration for s in script.scenes],
        headlines=[s.headline for s in script.scenes], visual_queries=queries)
    result = writer_script(draft, topic, research_data)
    assert "#AI" in result.hashtags and "#Shorts" in result.hashtags
    assert result.visual_keywords == queries
