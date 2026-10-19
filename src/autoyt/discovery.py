import hashlib
import logging
import math
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from urllib.parse import urlsplit
from xml.etree import ElementTree

import feedparser

from .models import Topic

log = logging.getLogger(__name__)
MAX_STORY_AGE_SECONDS = 7 * 24 * 60 * 60
TECH = {"ai", "artificial", "intelligence", "software", "processor", "chip", "chips", "laptop",
        "computer", "google", "microsoft", "openai", "apple", "android", "ios", "robot", "robotics",
        "cybersecurity", "security", "smartphone", "iphone", "samsung", "technology", "nvidia",
        "windows", "linux", "quantum", "gemini", "chatgpt", "gpu", "data", "browser",
        "apps", "app", "gadgets", "gadget", "smartwatch", "wearables", "headphones",
        "foldable", "pixel", "snapdragon", "qualcomm", "amd", "intel", "mediatek",
        "whatsapp", "instagram", "youtube", "oneplus", "smartphones", "processors"}
AI_CODING = {"ai", "llm", "llms", "artificial", "openai", "chatgpt", "gemini", "claude",
             "anthropic", "deepmind", "deepseek", "qwen", "llama", "mistral", "hugging",
             "huggingface", "coding", "code", "programming", "developer", "developers",
             "github", "copilot", "cursor", "codex", "ollama", "inference", "agents",
             "agentic", "multimodal", "grok", "xai"}
TECH |= AI_CODING
STOP = {"a", "an", "the", "is", "of", "to", "and", "in", "on", "for", "with", "that", "it",
        "this", "new", "has", "are", "from", "its", "as", "by", "be", "at", "can", "you"}


def words(text):
    # Keep combining marks with their letters, including Hindi vowel signs.
    tokens, token = set(), []
    for character in unicodedata.normalize("NFKC", text).casefold():
        if unicodedata.category(character)[0] in "LMN":
            token.append(character)
        elif token:
            tokens.add("".join(token))
            token = []
    if token:
        tokens.add("".join(token))
    return tokens - STOP


def normalized(text):
    return " ".join(sorted(words(text)))


def fingerprint(text):
    return hashlib.sha256(normalized(text).encode()).hexdigest()


def similarity(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / max(1, min(len(wa), len(wb)))


def duplicate(title, previous):
    title_tokens = normalized(title)
    return bool(title_tokens) and any(
        similarity(title, old) >= 0.8 or
        SequenceMatcher(None, title_tokens, normalized(old)).ratio() > 0.86
        for old in previous)


def repeated_insight(claims, previous):
    """A new headline does not make the same collection of verified facts new."""
    return bool(claims) and any(
        all(duplicate(claim, [c["claim"] for c in old.get("claims", [])]) for claim in claims)
        for old in previous)


def rank_insights(topics, recent_titles=(), focus="technology", slot=None):
    """Favor recent useful developments and variety without discarding evidence candidates."""
    categories = {
        "ai": {"ai", "artificial", "model", "models", "gemini", "chatgpt", "openai"},
        "development": {"coding", "developer", "developers", "programming", "github", "code"},
        "security": {"security", "privacy", "cybersecurity", "breach", "malware"},
        "hardware": {"chip", "chips", "processor", "processors", "gpu", "quantum"},
        "gadgets": {"phone", "phones", "smartphone", "pixel", "iphone", "gadgets", "laptop"},
        "software": {"app", "apps", "software", "windows", "browser", "linux"},
    }
    def category(text):
        tokens = words(text)
        return {name for name, terms in categories.items() if tokens & terms}

    recent = [category(title) for title in recent_titles]
    now = datetime.now(timezone.utc)

    def score(topic):
        if topic.content_kind == "evergreen":
            return -1  # Verified explainers are fallback after recent news.
        age = max(0, (now-topic.published_at).total_seconds())
        freshness = max(0, 1-age/MAX_STORY_AGE_SECONDS)
        tags = category(topic.title)
        repetition = sum(bool(tags & old) for old in recent)/max(1, len(recent))
        useful = bool(words(topic.title) & {
            "tool", "tools", "research", "benchmark", "explained", "developer", "developers",
            "coding", "privacy", "breakthrough", "launches", "releases", "introduces"})
        opinion = bool(re.search(r"\b(?:reasons why|i['’]ll|i think|rumou?r|leaked|best deals)\b",
                                 topic.title, re.I))
        preference = 1 if focus == "ai_coding" and words(topic.title) & AI_CODING else 0
        preferred_tags = {"08:00": {"ai", "development"}, "14:00": {"gadgets", "software"},
                          "20:00": {"ai", "development", "security", "hardware"}}.get((slot or "")[-5:], set())
        slot_bonus = 1.1 if tags & preferred_tags else 0
        recent_bonus = .15 if age <= 72*3600 else 0
        return preference + slot_bonus + recent_bonus + .6*topic.score + .2*freshness + .1*useful + .1*(1-repetition) - .35*opinion

    return sorted(topics, key=score, reverse=True)


def parse_feed(xml, source, now=None):
    now = now or datetime.now(timezone.utc)
    feed = feedparser.parse(xml)
    if feed.bozo and not feed.entries:
        raise ValueError("Invalid RSS/Atom feed")
    # feedparser retains only the last repeated Google Trends news_item. Keep all articles.
    trend_articles = {}
    if urlsplit(source).hostname == "trends.google.com":
        try:
            root = ElementTree.fromstring(xml)
            for item in root.findall("./channel/item")[:100]:
                links = [node.text.strip() for node in item.iter()
                         if node.tag.endswith("}news_item_url") and node.text
                         and urlsplit(node.text.strip()).scheme in {"http", "https"}]
                trend_articles[item.findtext("title", "")] = list(dict.fromkeys(links))
        except ElementTree.ParseError:
            pass
    result = []
    for entry in feed.entries[:100]:
        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        link = entry.get("link") or entry.get("id", "")
        if not parsed or urlsplit(link).scheme not in {"http", "https"} or not entry.get("title"):
            continue
        date = datetime(*parsed[:6], tzinfo=timezone.utc)
        age = (now - date).total_seconds() / 3600
        if not 0 <= age <= MAX_STORY_AGE_SECONDS / 3600 or not words(entry.title) & TECH:
            continue
        if re.search(r"\brumou?r|unconfirmed|reportedly", entry.title, re.I):
            continue
        traffic = entry.get("ht_approx_traffic", "")
        volume = int(re.sub(r"[^0-9]", "", traffic)) if re.search(r"\d", traffic) else None
        related = [v for k, v in entry.items() if k.endswith("news_item_url") and isinstance(v, str)]
        related = trend_articles.get(entry.title, related)
        topic = Topic(title=entry.title, url=related[0] if related else link,
                      published_at=date, source=source,
                      volume=volume, related_urls=related)
        relevance = min(1, len(words(entry.title) & TECH) / 3)
        strength = min(1, math.log10(max(1, volume)) / 6) if volume else None
        # Missing metrics are excluded; known weights are renormalized. Interest is a niche proxy.
        components = [(max(0, 1-age/(MAX_STORY_AGE_SECONDS / 3600)), .25),
                      (relevance, .25), (relevance, .20)]
        if strength is not None:
            components.append((strength, .30))
        topic.score = sum(v*w for v, w in components) / sum(w for _, w in components)
        result.append(topic)
    return result


def rank_topics(topics):
    # Reuse research's publisher families so duplicate feeds/owned sites cannot inflate interest.
    from .research import publisher

    unique = list({topic.url: topic for topic in reversed(topics)}.values())
    tokens = [words(topic.title) for topic in unique]
    publishers = [publisher(topic.url) for topic in unique]
    trends = [urlsplit(topic.source).hostname == "trends.google.com" for topic in unique]
    for i, topic in enumerate(unique):
        coverage = {publishers[i]}
        volume = topic.volume
        for j, other in enumerate(unique):
            if i == j:
                continue
            overlap = len(tokens[i] & tokens[j])
            match = overlap / max(1, min(len(tokens[i]), len(tokens[j])))
            is_trend = trends[i] or trends[j]
            if (overlap >= 3 and match >= .55) or (is_trend and overlap >= 2 and match >= .8):
                coverage.add(publishers[j])
                if other.volume:
                    volume = max(volume or 0, other.volume)
        # Coverage is a popularity proxy, not a claim that every article is a search trend.
        interest = min(1, (len(coverage) - 1) / 3)
        components = [(topic.score, .65), (interest, .20)]
        if volume:
            components.append((min(1, math.log10(max(1, volume)) / 6), .15))
        topic.score = sum(value * weight for value, weight in components) / sum(w for _, w in components)
    return sorted(unique, key=lambda item: item.score, reverse=True)


def discover(cfg, web):
    urls = [f"https://trends.google.com/trending/rss?geo={cfg.google_trends_region}", *cfg.rss_feeds]
    result = []
    for url in urls:
        try:
            result.extend(parse_feed(web.get(url), url))
        except Exception as error:
            log.warning("Feed unavailable: %s (%s)", url.split("?")[0], type(error).__name__)
    ranked = rank_topics(result)
    references, failed_channels = [], []
    for channel_id in cfg.creator_channel_ids:
        from urllib.parse import urlencode
        url = "https://www.youtube.com/feeds/videos.xml?" + urlencode({"channel_id": channel_id})
        try:
            references.extend(parse_feed(web.get(url), url))
        except Exception:
            failed_channels.append(channel_id)
    if failed_channels:
        try:
            from .youtube import creator_topics
            references.extend(creator_topics(cfg, failed_channels))
        except Exception as error:
            log.warning("Creator topic signals unavailable (%s); using verified news feeds", type(error).__name__)
    result = apply_creator_signals(ranked, references)
    if cfg.enable_evergreen_fallback:
        from .editorial import evergreen_topics
        result.extend(evergreen_topics())
    return result


def apply_creator_signals(topics, references):
    for topic in topics:
        matching = [r for r in references if len(words(topic.title) & words(r.title)) >= 2
                    and similarity(topic.title, r.title) >= .4]
        topic.creator_references = [{"title": r.title, "url": r.url, "source": r.source}
                                    for r in matching[:3]]
        if matching:
            topic.score = min(1, topic.score + .12)
    return sorted(topics, key=lambda t: t.score, reverse=True)
