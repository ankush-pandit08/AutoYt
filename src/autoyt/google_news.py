"""Recent technology leads from Google News RSS; headlines are never claim evidence."""
import logging
from datetime import datetime, timezone
from urllib.parse import urlencode, urlsplit

import feedparser

from . import discovery, limits
from .models import Topic

log = logging.getLogger(__name__)
QUERIES = (
    '(AI OR "large language model" OR "coding agent" OR software) '
    '(launch OR release OR update OR breakthrough) when:7d',
    '(technology OR robotics OR semiconductor OR smartphone OR quantum) '
    '(launch OR release OR update OR breakthrough) when:7d',
)


def parse(xml, now=None):
    from .youtube_content import is_product_development
    current = now or datetime.now(timezone.utc)
    topics = []
    for entry in feedparser.parse(xml).entries[:100]:
        title = entry.get("title", "").strip()
        source = entry.get("source", {})
        publisher = source.get("title", "")
        if publisher and title.endswith(" - " + publisher):
            title = title[:-(len(publisher) + 3)].strip()
        link = entry.get("link", "")
        parts = urlsplit(link)
        date = entry.get("published_parsed")
        if (not title or not date or parts.scheme != "https" or parts.hostname != "news.google.com"
                or parts.username or parts.password or not parts.path.startswith("/rss/articles/")):
            continue
        try:
            published = datetime(*date[:6], tzinfo=timezone.utc)
        except (ValueError, TypeError):
            continue
        age = (current - published).total_seconds()
        if not 0 <= age <= discovery.MAX_STORY_AGE_SECONDS:
            continue
        # Filter on the actual headline, not keywords stuffed into a feed summary.
        if not discovery.words(title) & discovery.TECH or not is_product_development(title):
            continue
        if discovery.words(title) & {"stocks", "shares", "earnings", "election", "celebrity", "rumor", "rumour",
                                      "jobs", "salaries", "careers", "reskilling", "valuation", "funding",
                                      "consultancy", "consulting", "academy", "authors", "books", "webinar",
                                      "summit", "awards", "marketing"}:
            continue
        topics.append(Topic(title=title, url=link, source="google_news_rss", published_at=published,
                            score=.5 + .3 * (1 - age / discovery.MAX_STORY_AGE_SECONDS)))
    return topics


def discover(cfg, web):
    if not cfg.google_news_enabled:
        return []
    topics, completed = [], 0
    # Small fixed number of public feeds; no API key or inference calls.
    with limits.research_budget(30), limits.request_limit(10):
        for query in QUERIES:
            try:
                url = "https://news.google.com/rss/search?" + urlencode(
                    {"q": query, "hl": "en-IN", "gl": "IN", "ceid": "IN:en"})
                topics.extend(parse(web.get(url, max_bytes=1_500_000)))
                completed += 1
            except Exception as error:
                log.warning("Google News RSS feed unavailable (%s)", type(error).__name__)
    if not completed:
        raise RuntimeError("TRANSIENT: Google News RSS is temporarily unavailable; retry later")
    unique = []
    for topic in sorted(topics, key=lambda t: t.score, reverse=True):
        if not discovery.duplicate(topic.title, [t.title for t in unique]):
            unique.append(topic)
    return unique
