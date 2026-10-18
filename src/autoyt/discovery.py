import hashlib
import logging
import math
import re
from datetime import datetime, timezone
from difflib import SequenceMatcher

import feedparser

from .models import Topic

log = logging.getLogger(__name__)
TECH = {"ai", "artificial", "intelligence", "software", "processor", "chip", "chips", "laptop",
        "computer", "google", "microsoft", "openai", "apple", "android", "ios", "robot", "robotics",
        "cybersecurity", "security", "smartphone", "iphone", "samsung", "technology", "nvidia",
        "windows", "linux", "quantum", "gemini", "chatgpt", "gpu", "data", "browser"}
STOP = {"a", "an", "the", "is", "of", "to", "and", "in", "on", "for", "with", "that", "it",
        "this", "new", "has", "are", "from", "its", "as", "by", "be", "at", "can", "you"}


def words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower())) - STOP


def normalized(text):
    return " ".join(sorted(words(text)))


def fingerprint(text):
    return hashlib.sha256(normalized(text).encode()).hexdigest()


def similarity(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def duplicate(title, previous):
    return any(similarity(title, old) >= 0.8 or
               SequenceMatcher(None, normalized(title), normalized(old)).ratio() > 0.86
               for old in previous)


def parse_feed(xml, source, now=None):
    now = now or datetime.now(timezone.utc)
    feed = feedparser.parse(xml)
    if feed.bozo and not feed.entries:
        raise ValueError("Invalid RSS/Atom feed")
    result = []
    for entry in feed.entries[:100]:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if not parsed or not entry.get("link") or not entry.get("title"):
            continue
        date = datetime(*parsed[:6], tzinfo=timezone.utc)
        age = (now - date).total_seconds() / 3600
        if not 0 <= age <= 24 or not words(entry.title) & TECH:
            continue
        if re.search(r"\brumou?r|unconfirmed|reportedly", entry.title, re.I):
            continue
        traffic = entry.get("ht_approx_traffic", "")
        volume = int(re.sub(r"[^0-9]", "", traffic)) if re.search(r"\d", traffic) else None
        related = [v for k, v in entry.items() if k.endswith("news_item_url") and isinstance(v, str)]
        topic = Topic(title=entry.title, url=entry.link, published_at=date, source=source,
                      volume=volume, related_urls=related)
        relevance = min(1, len(words(entry.title) & TECH) / 3)
        strength = min(1, math.log10(max(1, volume)) / 6) if volume else None
        # Missing metrics are excluded; known weights are renormalized. Interest is a niche proxy.
        components = [(max(0, 1-age/24), .25), (relevance, .25), (relevance, .20)]
        if strength is not None:
            components.append((strength, .30))
        topic.score = sum(v*w for v, w in components) / sum(w for _, w in components)
        result.append(topic)
    return result


def discover(cfg, web):
    urls = [f"https://trends.google.com/trending/rss?geo={cfg.google_trends_region}", *cfg.rss_feeds]
    result = []
    for url in urls:
        try:
            result.extend(parse_feed(web.get(url), url))
        except Exception as error:
            log.warning("Feed unavailable: %s (%s)", url.split("?")[0], type(error).__name__)
    return sorted(result, key=lambda item: item.score, reverse=True)
