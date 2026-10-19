from datetime import datetime, timezone
from email.utils import format_datetime

import pytest

from autoyt.discovery import parse_feed, rank_topics
from autoyt.models import Topic
from autoyt.research import publication_hint


def topic(title, url, **kwargs):
    return Topic(title=title, url=url, published_at=datetime.now(timezone.utc),
                 source=kwargs.pop("source", url), score=.7, **kwargs)


@pytest.mark.parametrize("title", ["WhatsApp launches message translation", "Qualcomm unveils Snapdragon chip",
                                  "OnePlus introduces foldable smartphone", "New gadgets announced"])
def test_broad_technology_topics(title):
    xml = f"""<rss><channel><item><title>{title}</title><link>https://example.com/news</link>
    <pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate></item></channel></rss>"""
    assert len(parse_feed(xml, "test")) == 1


def test_trends_preserves_all_article_links():
    xml = f"""<rss xmlns:ht="https://trends.google.com/trending/rss"><channel><item>
    <title>Google Pixel</title><link>https://trends.google.com/trending/rss?geo=IN</link>
    <pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate><ht:approx_traffic>50,000+</ht:approx_traffic>
    <ht:news_item><ht:news_item_url>https://blog.google/pixel</ht:news_item_url></ht:news_item>
    <ht:news_item><ht:news_item_url>https://example.com/pixel</ht:news_item_url></ht:news_item>
    </item></channel></rss>"""
    result = parse_feed(xml, "https://trends.google.com/trending/rss?geo=IN")[0]
    assert result.url == "https://blog.google/pixel"
    assert result.related_urls == ["https://blog.google/pixel", "https://example.com/pixel"]
    assert result.volume == 50000
    assert publication_hint(result) is None
    article = topic("Google Pixel announcement", "https://blog.google/pixel")
    assert publication_hint(article) == article.published_at


def test_independent_coverage_ranks_above_isolated_story():
    isolated = topic("Microsoft Windows introduces desktop widgets", "https://microsoft.com/widgets")
    google = topic("Google Pixel launches satellite messaging", "https://blog.google/pixel")
    coverage = topic("Google Pixel satellite messaging launches today", "https://theverge.com/pixel")
    ranked = rank_topics([isolated, google, coverage])
    assert ranked.index(google) < ranked.index(isolated)


def test_same_publisher_and_repeated_feed_do_not_inflate_coverage():
    first = topic("Google Pixel launches satellite messaging", "https://blog.google/pixel")
    same = topic(first.title, "https://news.google.com/pixel")
    repeated = first.model_copy(deep=True)
    expected = rank_topics([first.model_copy(deep=True)])[0].score
    ranked = rank_topics([first, same, repeated])
    assert len(ranked) == 2
    assert all(t.score == expected for t in ranked)


def test_real_search_interest_boosts_matching_news_only():
    pixel = topic("Google Pixel satellite messaging launches", "https://blog.google/pixel")
    unrelated = topic("Google Gemini launches coding assistant", "https://blog.google/gemini")
    trend = topic("Google Pixel", "https://example.com/pixel", volume=100000,
                  source="https://trends.google.com/trending/rss?geo=IN")
    ranked = rank_topics([unrelated, pixel, trend])
    assert ranked.index(pixel) < ranked.index(unrelated)
    assert unrelated.volume is None


@pytest.mark.parametrize("title", ["Claude launches agentic coding", "DeepSeek releases LLM inference update",
                                  "Cursor introduces developer tools", "Qwen releases multimodal models"])
def test_ai_developer_headlines_are_discovered(title):
    xml = f"""<rss><channel><item><title>{title}</title><link>https://example.com/news</link>
    <pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate></item></channel></rss>"""
    assert len(parse_feed(xml, "test")) == 1


def test_feed_permalink_guid_without_link():
    xml = f"""<rss><channel><item><title>LLM coding update</title>
    <guid isPermaLink="true">https://huggingface.co/blog/llm-update</guid>
    <pubDate>{format_datetime(datetime.now(timezone.utc))}</pubDate></item></channel></rss>"""
    assert parse_feed(xml, "test")[0].url == "https://huggingface.co/blog/llm-update"


def test_ai_focus_prioritizes_ai_but_keeps_general_tech_fallback():
    from autoyt.discovery import rank_insights
    gadget = topic("Samsung phone update", "https://example.com/phone")
    ai = topic("Claude agentic coding update", "https://example.com/claude")
    ai.score = .1
    gadget.score = 1
    assert rank_insights([gadget, ai], focus="ai_coding") == [ai, gadget]


def test_official_links_prioritize_story_over_navigation(cfg):
    from autoyt.research import official_links, publisher
    html = '''<a href="https://blog.google/security/rss/">RSS</a>
    <a href="https://blog.google/">Home</a><a href="https://blog.google/about/">About</a>
    <a href="https://blog.google/pixel/new-phone/">Phone</a>
    <a href="https://blog.google/ai/gemini-coding-update/">Announcement</a>'''
    links = official_links(html, "https://example.com/story", "Gemini coding update", cfg)
    assert links == ["https://blog.google/ai/gemini-coding-update/", "https://blog.google/pixel/new-phone/"]
    assert publisher("https://deepmind.google/blog/update") == publisher("https://blog.google/ai/update")
    assert publisher("https://github.blog/update") == publisher("https://microsoft.com/update")
