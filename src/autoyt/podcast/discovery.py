"""Episode discovery for the podcast clipping platform.

Discovers YouTube podcast episodes from configured sources using the YouTube
Data API v3 (via the existing googleapiclient integration) with a fallback
to channel RSS feeds.  Episodes are scored by topic relevance against each
source's priority_topics list.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .sources import PodcastSource, SourceRegistry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Recency window for episode discovery (30 days)
DISCOVERY_WINDOW_DAYS = 30

# Preferred podcast duration range (seconds)
PREFER_MIN_SECONDS = 30 * 60   # 30 min
PREFER_MAX_SECONDS = 120 * 60  # 120 min

# Global topic keyword set for scoring (superset of per-source topics)
TOPIC_KEYWORDS: set[str] = {
    "motivation", "entrepreneurship", "startup", "success", "failure",
    "discipline", "career", "money", "business", "psychology", "mindset",
    "productivity", "leadership", "innovation", "debate", "opinion",
    "investing", "finance", "philosophy", "health", "interview", "podcast",
    "wealth", "growth", "habits", "risk", "strategy", "vision", "purpose",
}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class PodcastEpisode:
    """A discovered podcast episode."""

    id: str                       # Unique key: "{source_id}:{video_id}"
    source_id: str
    video_id: str                 # YouTube video ID
    video_url: str                # https://www.youtube.com/watch?v=...
    title: str
    channel_name: str
    published_at: datetime
    duration_seconds: int         # 0 if unknown
    thumbnail_url: str
    description: str
    discovery_score: float        # 0.0–1.0
    topics_matched: list[str]     # Subset of priority_topics that matched
    already_processed: bool
    discovered_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_id": self.source_id,
            "video_id": self.video_id,
            "video_url": self.video_url,
            "title": self.title,
            "channel_name": self.channel_name,
            "published_at": self.published_at.isoformat(),
            "duration_seconds": self.duration_seconds,
            "thumbnail_url": self.thumbnail_url,
            "description": self.description[:500],  # truncate for display
            "discovery_score": round(self.discovery_score, 4),
            "topics_matched": self.topics_matched,
            "already_processed": self.already_processed,
            "discovered_at": self.discovered_at.isoformat(),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _words(text: str) -> set[str]:
    """Return a normalised set of lowercase word tokens."""
    tokens, token = set(), []
    stop = {"a", "an", "the", "is", "of", "to", "and", "in", "on", "for",
            "with", "that", "it", "this", "new", "has", "are", "from", "its",
            "as", "by", "be", "at", "can", "you", "i", "my", "we", "our"}
    for ch in unicodedata.normalize("NFKC", text).casefold():
        if unicodedata.category(ch)[0] in "LMN":
            token.append(ch)
        elif token:
            tokens.add("".join(token))
            token = []
    if token:
        tokens.add("".join(token))
    return tokens - stop


def _parse_iso8601_duration(duration: str) -> int:
    """Convert ISO 8601 duration string (PT1H2M3S) to total seconds."""
    pattern = re.compile(
        r"P(?:(\d+)Y)?(?:(\d+)M)?(?:(\d+)W)?(?:(\d+)D)?"
        r"(?:T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?)?"
    )
    m = pattern.fullmatch(duration or "")
    if not m:
        return 0
    years, months, weeks, days, hours, minutes, seconds = (int(v or 0) for v in m.groups())
    return (years * 365 * 86400 + months * 30 * 86400 + weeks * 7 * 86400
            + days * 86400 + hours * 3600 + minutes * 60 + seconds)


def _thumbnail(item: dict) -> str:
    """Extract the best available thumbnail URL from a YouTube snippet."""
    thumbs = item.get("snippet", {}).get("thumbnails", {})
    for quality in ("maxres", "standard", "high", "medium", "default"):
        if quality in thumbs:
            return thumbs[quality].get("url", "")
    return ""


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def score_episode(episode: PodcastEpisode, source: PodcastSource) -> float:
    """Score an episode 0.0–1.0 based on relevance and recency.

    Components:
      - Topic keyword match (0.0–0.4): how many priority_topics appear in title+description
      - Recency (0.0–0.3): exponential decay over DISCOVERY_WINDOW_DAYS
      - Duration fit (0.0–0.3): prefer 30–120 min podcasts

    Returns the weighted score and updates episode.topics_matched.
    """
    text_tokens = _words(episode.title + " " + episode.description)
    matched = [t for t in source.priority_topics if t in text_tokens]
    episode.topics_matched = matched

    # Topic score: at least 3 matches → full weight
    topic_score = min(1.0, len(matched) / max(1, min(3, len(source.priority_topics))))

    # Recency score
    age_seconds = max(0.0, (datetime.now(timezone.utc) - episode.published_at).total_seconds())
    max_age = DISCOVERY_WINDOW_DAYS * 86400
    recency_score = max(0.0, 1.0 - age_seconds / max_age)

    # Duration score
    dur = episode.duration_seconds
    if dur <= 0:
        duration_score = 0.5  # Unknown → neutral
    elif PREFER_MIN_SECONDS <= dur <= PREFER_MAX_SECONDS:
        duration_score = 1.0
    elif dur < PREFER_MIN_SECONDS:
        duration_score = dur / PREFER_MIN_SECONDS
    else:
        # Penalise very long episodes gently
        duration_score = max(0.0, 1.0 - (dur - PREFER_MAX_SECONDS) / (3 * 3600))

    score = 0.4 * topic_score + 0.3 * recency_score + 0.3 * duration_score
    return round(min(1.0, max(0.0, score)), 4)


# ---------------------------------------------------------------------------
# Discovery via YouTube Data API
# ---------------------------------------------------------------------------

class EpisodeDiscovery:
    """Discovers podcast episodes from configured sources.

    Requires the YouTube Data API v3; uses the same OAuth service as youtube.py
    when credentials are present, or a plain API key otherwise.

    When no API key / credentials are configured, falls back to the channel's
    public RSS feed (https://www.youtube.com/feeds/videos.xml?channel_id=…).
    """

    def __init__(self, cfg, store=None):
        """
        Args:
            cfg: Settings instance (must have YOUTUBE_DATA_API_KEY or OAuth token).
            store: Store instance for checking already-processed episodes (optional).
        """
        self.cfg = cfg
        self.store = store

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover_episodes(
        self,
        source: PodcastSource,
        max_results: int = 10,
    ) -> list[PodcastEpisode]:
        """Discover recent podcast episodes for a single source.

        Tries YouTube Data API first, then RSS fallback.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=DISCOVERY_WINDOW_DAYS)
        episodes: list[PodcastEpisode] = []

        try:
            episodes = self._discover_via_api(source, max_results, cutoff)
        except Exception as exc:
            log.warning(
                "YouTube API unavailable for %s (%s); trying RSS fallback",
                source.id, type(exc).__name__,
            )
            try:
                episodes = self._discover_via_rss(source, max_results, cutoff)
            except Exception as rss_exc:
                log.warning(
                    "RSS fallback also failed for %s: %s",
                    source.id, rss_exc,
                )

        # Score and filter
        already_ids = self._already_processed_ids(source.id)
        for ep in episodes:
            ep.already_processed = ep.video_id in already_ids
            ep.discovery_score = score_episode(ep, source)

        episodes.sort(key=lambda e: e.discovery_score, reverse=True)
        return episodes

    def discover_all_sources(self, registry: SourceRegistry) -> list[PodcastEpisode]:
        """Run discovery for all enabled sources; return sorted by score descending."""
        all_episodes: list[PodcastEpisode] = []
        for source in registry.enabled():
            log.info("Discovering episodes for %s (%s)", source.name, source.id)
            episodes = self.discover_episodes(source)
            # Only return unprocessed episodes
            all_episodes.extend(ep for ep in episodes if not ep.already_processed)

        all_episodes.sort(key=lambda e: e.discovery_score, reverse=True)
        return all_episodes

    # ------------------------------------------------------------------
    # YouTube Data API discovery
    # ------------------------------------------------------------------

    def _build_youtube_service(self):
        """Build a YouTube Data API service using API key or OAuth credentials.

        Raises RuntimeError with a user-friendly message when neither is available.
        """
        api_key = getattr(self.cfg, "youtube_data_api_key", "").strip()

        if api_key:
            from googleapiclient.discovery import build
            return build("youtube", "v3", developerKey=api_key, cache_discovery=False)

        # Try OAuth (requires tokens on disk)
        token_file = getattr(self.cfg, "youtube_token_file", None)
        if token_file and token_file.is_file():
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
            creds = Credentials.from_authorized_user_file(str(token_file))
            return build("youtube", "v3", credentials=creds, cache_discovery=False)

        raise RuntimeError(
            "No YouTube API key configured. Set YOUTUBE_DATA_API_KEY in .env "
            "or run 'autoyt auth' to set up OAuth for the Data API."
        )

    def _discover_via_api(
        self,
        source: PodcastSource,
        max_results: int,
        cutoff: datetime,
    ) -> list[PodcastEpisode]:
        """Discover episodes via YouTube Data API v3."""
        youtube = self._build_youtube_service()

        # Resolve playlist ID
        playlist_id = source.playlist_id
        if not playlist_id:
            playlist_id = self._get_uploads_playlist(youtube, source.youtube_channel_id)

        # Fetch recent uploads
        items = self._fetch_playlist_items(youtube, playlist_id, max_results * 2)

        # Fetch video details (duration, full description)
        video_ids = [
            item["contentDetails"]["videoId"]
            for item in items
            if "contentDetails" in item
        ]
        details = self._fetch_video_details(youtube, video_ids) if video_ids else {}

        episodes = []
        for item in items:
            snippet = item.get("snippet", {})
            video_id = item.get("contentDetails", {}).get("videoId", "")
            if not video_id:
                continue

            published_str = snippet.get("publishedAt") or item["contentDetails"].get(
                "videoPublishedAt", ""
            )
            try:
                published_at = datetime.fromisoformat(
                    published_str.replace("Z", "+00:00")
                )
            except (ValueError, AttributeError):
                continue

            if published_at < cutoff:
                continue

            detail = details.get(video_id, {})
            duration_str = detail.get("contentDetails", {}).get("duration", "")
            duration_seconds = _parse_iso8601_duration(duration_str)
            channel_name = detail.get("snippet", {}).get("channelTitle") or source.name
            description = (
                detail.get("snippet", {}).get("description")
                or snippet.get("description", "")
            )

            ep = PodcastEpisode(
                id=f"{source.id}:{video_id}",
                source_id=source.id,
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=snippet.get("title", ""),
                channel_name=channel_name,
                published_at=published_at,
                duration_seconds=duration_seconds,
                thumbnail_url=_thumbnail(item),
                description=description,
                discovery_score=0.0,
                topics_matched=[],
                already_processed=False,
            )
            episodes.append(ep)
            if len(episodes) >= max_results:
                break

        return episodes

    def _get_uploads_playlist(self, youtube, channel_id: str) -> str:
        """Return the uploads playlist ID for a channel."""
        response = youtube.channels().list(
            part="contentDetails",
            id=channel_id,
        ).execute()
        items = response.get("items", [])
        if not items:
            raise ValueError(f"Channel {channel_id} not found or no contentDetails")
        return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]

    def _fetch_playlist_items(self, youtube, playlist_id: str, max_results: int) -> list:
        """Fetch items from a playlist."""
        from googleapiclient.errors import HttpError
        try:
            response = youtube.playlistItems().list(
                part="snippet,contentDetails",
                playlistId=playlist_id,
                maxResults=min(max_results, 50),
            ).execute()
            return response.get("items", [])
        except HttpError as exc:
            if exc.resp.status in (403, 404):
                log.warning("Playlist %s unavailable: %s", playlist_id, exc)
                return []
            raise

    def _fetch_video_details(self, youtube, video_ids: list[str]) -> dict:
        """Batch-fetch video details; returns {video_id: item_dict}."""
        if not video_ids:
            return {}
        response = youtube.videos().list(
            part="snippet,contentDetails",
            id=",".join(video_ids[:50]),
        ).execute()
        return {item["id"]: item for item in response.get("items", [])}

    # ------------------------------------------------------------------
    # RSS fallback
    # ------------------------------------------------------------------

    def _discover_via_rss(
        self,
        source: PodcastSource,
        max_results: int,
        cutoff: datetime,
    ) -> list[PodcastEpisode]:
        """Discover episodes via the channel's public RSS feed."""
        import urllib.request

        import feedparser

        feed_url = (
            source.rss_feed_url
            or f"https://www.youtube.com/feeds/videos.xml?channel_id={source.youtube_channel_id}"
        )
        log.info("Fetching RSS feed: %s", feed_url)
        req = urllib.request.Request(
            feed_url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"},
        )
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                xml_data = resp.read()
        except Exception as exc:
            raise ValueError(f"Could not fetch RSS feed for {source.id}: {exc}") from exc

        feed = feedparser.parse(xml_data)
        if feed.bozo and not feed.entries:
            raise ValueError(f"Could not parse RSS feed for {source.id}: {feed_url}")

        episodes = []
        for entry in feed.entries[:max_results * 2]:
            parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if not parsed:
                continue
            published_at = datetime(*parsed[:6], tzinfo=timezone.utc)
            if published_at < cutoff:
                continue

            # Extract YouTube video ID from the entry link
            link = entry.get("link", "")
            m = re.search(r"[?&]v=([A-Za-z0-9_-]{11})", link)
            if not m:
                # Atom feed from YouTube uses yt:videoId
                video_id = entry.get("yt_videoid") or entry.get("id", "").split(":")[-1]
            else:
                video_id = m.group(1)
            if not video_id:
                continue

            # Check thumbnail from media_thumbnail
            thumb_url = ""
            if "media_thumbnail" in entry and entry.media_thumbnail:
                thumb_url = entry.media_thumbnail[0].get("url", "")
            elif not thumb_url and video_id:
                thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

            summary = entry.get("summary", "")
            if "media_description" in entry:
                summary = entry.media_description or summary

            ep = PodcastEpisode(
                id=f"{source.id}:{video_id}",
                source_id=source.id,
                video_id=video_id,
                video_url=f"https://www.youtube.com/watch?v={video_id}",
                title=entry.get("title", ""),
                channel_name=source.name,
                published_at=published_at,
                duration_seconds=0,  # RSS doesn't include duration
                thumbnail_url=thumb_url,
                description=summary,
                discovery_score=0.0,
                topics_matched=[],
                already_processed=False,
            )
            episodes.append(ep)
            if len(episodes) >= max_results:
                break

        return episodes

    # ------------------------------------------------------------------
    # Store integration
    # ------------------------------------------------------------------

    def _already_processed_ids(self, source_id: str) -> set[str]:
        """Return video IDs that have already been processed for this source."""
        if self.store is None:
            return set()
        try:
            episodes = self.store.list_podcast_episodes(
                source_id=source_id,
                status="processed",
                limit=500,
            )
            return {ep["video_id"] for ep in episodes}
        except Exception:
            return set()
