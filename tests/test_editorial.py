from datetime import timedelta

from test_discovery import topic

from autoyt.discovery import apply_creator_signals, rank_insights, repeated_insight
from autoyt.store import Store


def test_new_headline_cannot_reuse_the_same_verified_insight():
    claims = ["Processors execute computing instructions.", "Memory stores working data for programs."]
    old = {"claims": [{"claim": c} for c in claims]}
    assert repeated_insight(claims[::-1], [old])
    assert not repeated_insight([claims[0], "Browsers introduce additional tracking protections."], [old])
    assert not repeated_insight([], [old])


def test_insight_ranking_favors_recent_developments_and_variety():
    old = topic("Google AI introduces coding tools", "https://example.com/old")
    old.published_at -= timedelta(days=5)
    fresh = topic(old.title, "https://example.com/new")
    assert rank_insights([old, fresh])[0] is fresh
    software = topic("Windows software releases browser controls", "https://example.com/software")
    hardware = topic("Quantum chip releases processor benchmark", "https://example.com/hardware")
    hardware.published_at = software.published_at
    assert rank_insights([software, hardware], ["Windows software browser update"])[0] is hardware


def test_creator_signals_boost_related_news_without_becoming_evidence():
    related = topic("Google Gemini introduces coding tools", "https://blog.google/tools")
    other = topic("Windows browser adds privacy controls", "https://example.com/privacy")
    reference = topic("Google Gemini coding tools explained", "https://www.youtube.com/watch?v=reference")
    ranked = apply_creator_signals([other, related], [reference])
    assert ranked[0] is related
    assert related.url == "https://blog.google/tools"
    assert related.related_urls == []
    assert related.creator_references[0]["url"] == reference.url
    assert other.creator_references == []


def test_research_history_ignores_previews_and_failed_unuploaded_jobs(cfg, research_data):
    db = Store(cfg.database_path)
    for dry_run, state in [(True, "READY_TO_UPLOAD"), (False, "FAILED"), (False, "PUBLISHED")]:
        job = db.create(dry_run=dry_run)
        db.save(job, "research", research_data.model_dump(mode="json"))
        db.update(job, state)
    assert len(db.recent_research(14)) == 1
