from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace
from xml.sax.saxutils import escape

import pytest

from autoyt import discovery, google_news, youtube_content
from autoyt.pipeline import Pipeline


def feed(*titles, age=1):
    date = format_datetime(datetime.now(timezone.utc) - timedelta(days=age))
    return '<rss version="2.0"><channel>' + ''.join(
        f'<item><title>{escape(title)} - Tech Publisher</title>'
        f'<link>https://news.google.com/rss/articles/story{i}</link><pubDate>{date}</pubDate>'
        '<source url="https://publisher.example">Tech Publisher</source></item>'
        for i, title in enumerate(titles)) + '</channel></rss>'


def news_topic():
    return google_news.parse(feed("OpenAI releases new coding model"))[0]


def test_tech_filter_dates_and_metadata():
    topics = google_news.parse(feed("OpenAI releases new coding model", "Celebrity announces wedding",
        "Microsoft shares rise after earnings", "AI productivity tips", "New quantum processor breakthrough",
        "Anthropic strikes deal to launch European consultancy", "AI Academy launches new books for authors"))
    assert [t.title for t in topics] == ["OpenAI releases new coding model", "New quantum processor breakthrough"]
    assert all(t.source == "google_news_rss" and not t.youtube_metadata for t in topics)
    assert not google_news.parse(feed("OpenAI releases new coding model", age=8))
    assert not google_news.parse(feed("OpenAI releases new coding model", age=-1))
    assert not google_news.parse(feed("OpenAI releases new coding model").replace(
        "https://news.google.com/rss/articles/", "https://evil.example/"))


def test_bounded_rss_queries_without_key_or_model(cfg):
    cfg.google_news_enabled = True
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return feed("OpenAI releases new coding model")
    result = google_news.discover(cfg, SimpleNamespace(get=get))
    assert len(result) == 1 and len(calls) == 2
    assert all(url.startswith("https://news.google.com/rss/search?") and "when%3A7d" in url for url, _ in calls)
    assert all(kwargs["max_bytes"] == 1_500_000 for _, kwargs in calls)


def test_creator_failure_does_not_block_news(cfg, monkeypatch):
    monkeypatch.setattr(youtube_content, "discover", lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("YouTube unavailable")))
    monkeypatch.setattr(google_news, "discover", lambda *a: [news_topic()])
    assert discovery.creator_and_news(cfg, None)[0].source == "google_news_rss"


def test_news_failure_does_not_block_creator(cfg, monkeypatch):
    creator = news_topic().model_copy(update={"source": "youtube"})
    monkeypatch.setattr(youtube_content, "discover", lambda *a, **kw: [creator])
    monkeypatch.setattr(google_news, "discover", lambda *a: (_ for _ in ()).throw(RuntimeError("RSS unavailable")))
    assert discovery.creator_and_news(cfg, None) == [creator]
    monkeypatch.setattr(youtube_content, "discover", lambda *a, **kw: [])
    with pytest.raises(RuntimeError, match="sources unavailable"):
        discovery.creator_and_news(cfg, None)


@pytest.mark.parametrize("fresh", [False, True])
def test_used_creator_pool_selects_new_news_topic(cfg, monkeypatch, fresh):
    cfg.content_mode = "youtube_insights"
    cfg.prefer_tech_insights = True
    creator = news_topic().model_copy(update={"title": "Tesla launches flying roadster", "source": "youtube",
        "url": "https://www.youtube.com/watch?v=abc123DEF45", "score": .1})
    new = news_topic()
    monkeypatch.setattr(youtube_content, "discover", lambda *a, **kw: [creator])
    monkeypatch.setattr(google_news, "discover", lambda *a: [new])
    assert discovery.rank_insights([new, creator], focus="ai_coding")[0] == creator
    runner = Pipeline(cfg)
    old = runner.store.create()
    runner.store.update(old, "FAILED", topic=creator.title)
    monkeypatch.setattr(youtube_content, "research_brief", lambda *a: (_ for _ in ()).throw(RuntimeError("Research reached")))
    job = runner.create_run(fresh=fresh)
    assert runner.store.get(job)["topic"] == new.title
    # Blocking the news story too must never recycle either story.
    runner.store.update(job, "FAILED")
    next_job = runner.create_run(fresh=fresh)
    assert runner.store.get(next_job)["state"] == "SKIPPED"


def test_rss_headline_is_reference_and_requires_article_research(cfg, monkeypatch, research_data):
    topic = news_topic()
    brief = youtube_content.brief(topic)
    assert brief.basis == "google_news" and brief.evidence is None and not brief.videos
    with pytest.raises(ValueError, match="Supporting article research is missing"):
        youtube_content.generate(cfg, None, topic, brief)
    monkeypatch.setattr("autoyt.topic_research.gather", lambda *a: research_data)
    supported = youtube_content.research_brief(cfg, None, None, topic)
    assert supported.evidence == research_data
    details = youtube_content.selection_details(topic, supported)
    assert details["sources"] == [{"title": topic.title, "url": topic.url}]
    assert details["article_sources"] and "Google News RSS" in details["reason"]
