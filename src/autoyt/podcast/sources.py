"""Podcast Source Registry — creator/channel registry with configurable JSON overrides.

Channel IDs below are placeholders.  Verify against actual YouTube channel pages
before running live discovery.  To correct them without touching code, edit
  data/podcast_sources.json
or set PODCAST_SOURCES_FILE in .env to point at another JSON file.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Default JSON override file (relative to cwd, same as other data/ paths in config.py)
DEFAULT_SOURCES_FILE = Path("data/podcast_sources.json")

# ---------------------------------------------------------------------------
# Topic keywords used for scoring (shared with discovery.py)
# ---------------------------------------------------------------------------
PODCAST_TOPICS = {
    "motivation", "entrepreneurship", "startup", "success", "failure",
    "discipline", "career", "money", "business", "psychology", "mindset",
    "productivity", "leadership", "innovation", "debate", "opinion",
    "investing", "finance", "philosophy", "health",
}


@dataclass
class PodcastSource:
    """A single configured podcast creator/channel."""

    id: str                       # Unique slug e.g. "raj_shamani"
    name: str                     # "Raj Shamani"
    show_name: str                # "Figuring Out"
    # TODO: Verify these channel IDs against actual YouTube channel pages.
    # They are configurable via data/podcast_sources.json without code changes.
    youtube_channel_id: str       # UC… identifier
    youtube_channel_url: str      # https://www.youtube.com/@Handle
    playlist_id: Optional[str]    # Uploads playlist (UU…) or None to auto-derive
    rss_feed_url: Optional[str]   # Optional RSS/Atom feed URL
    website_url: Optional[str]    # Creator's website
    enabled: bool                 # Whether to include in discovery runs
    priority_topics: list[str]    # Topic keywords for scoring
    created_at: datetime          # When this record was added

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "PodcastSource":
        d = dict(data)
        raw_dt = d.get("created_at")
        if isinstance(raw_dt, str):
            try:
                d["created_at"] = datetime.fromisoformat(raw_dt)
            except ValueError:
                d["created_at"] = datetime.now(timezone.utc)
        elif not isinstance(raw_dt, datetime):
            d["created_at"] = datetime.now(timezone.utc)
        # Ensure tz-aware
        if d["created_at"].tzinfo is None:
            d["created_at"] = d["created_at"].replace(tzinfo=timezone.utc)
        return cls(**d)


# ---------------------------------------------------------------------------
# Built-in defaults
# (Channel IDs are placeholders — override via data/podcast_sources.json)
# ---------------------------------------------------------------------------
_BUILTIN_DEFAULTS: list[dict] = [
    {
        "id": "raj_shamani",
        "name": "Raj Shamani",
        "show_name": "Figuring Out",
        "youtube_channel_id": "UCshPyRiu9XTQb2gEagNt-3A",
        "youtube_channel_url": "https://www.youtube.com/@RajShamani",
        "playlist_id": None,
        "rss_feed_url": None,
        "website_url": "https://rajshamani.com",
        "enabled": True,
        "priority_topics": [
            "entrepreneurship", "motivation", "startup", "success",
            "mindset", "business", "money", "leadership", "productivity", "career",
        ],
        "created_at": "2026-11-17T00:00:00+00:00",
    },
    {
        "id": "ranveer_allahbadia",
        "name": "Ranveer Allahbadia",
        "show_name": "The Ranveer Show",
        "youtube_channel_id": "UCPxMZIFE856tbTfdkdjzTSQ",
        "youtube_channel_url": "https://www.youtube.com/@RanveerAllahbadia",
        "playlist_id": None,
        "rss_feed_url": None,
        "website_url": None,
        "enabled": True,
        "priority_topics": [
            "entrepreneurship", "psychology", "mindset", "success",
            "discipline", "philosophy", "health", "motivation", "business", "innovation",
        ],
        "created_at": "2026-11-17T00:00:00+00:00",
    },
    {
        "id": "nikhil_kamath",
        "name": "Nikhil Kamath",
        "show_name": "WTF is",
        "youtube_channel_id": "UCRv4waLxgUN0Z-yb2I1Fq4A",
        "youtube_channel_url": "https://www.youtube.com/@nikhil.kamath",
        "playlist_id": None,
        "rss_feed_url": None,
        "website_url": None,
        "enabled": True,
        "priority_topics": [
            "business", "investing", "money", "startup", "entrepreneurship",
            "debate", "opinion", "finance", "career", "innovation",
        ],
        "created_at": "2026-11-17T00:00:00+00:00",
    },
    {
        "id": "prakhar_gupta",
        "name": "Prakhar Gupta",
        "show_name": "Prakhar ke Pravachan",
        "youtube_channel_id": "UCHOKvQW2N4kLVhKYn2bvF7A",
        "youtube_channel_url": "https://www.youtube.com/@PrakharkePravachan",
        "playlist_id": None,
        "rss_feed_url": None,
        "website_url": None,
        "enabled": True,
        "priority_topics": [
            "psychology", "philosophy", "mindset", "debate", "opinion",
            "motivation", "humor", "serious", "culture", "life lessons",
        ],
        "created_at": "2026-11-17T00:00:00+00:00",
    },
]


class SourceRegistry:
    """Registry of podcast creator sources.

    Load order (later wins):
      1. Built-in defaults (hardcoded above)
      2. ``sources_file`` JSON overrides (default: data/podcast_sources.json)

    The JSON file is authoritative for channel IDs and other mutable config.
    """

    def __init__(self, sources_file: Optional[Path] = None):
        self._file = sources_file if sources_file is not None else DEFAULT_SOURCES_FILE
        self._sources: dict[str, PodcastSource] = {}
        self._load()

    # ------------------------------------------------------------------
    # Loading / persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load built-in defaults then apply JSON file overrides."""
        for raw in _BUILTIN_DEFAULTS:
            src = PodcastSource.from_dict(raw)
            self._sources[src.id] = src

        if self._file.is_file():
            try:
                data = json.loads(self._file.read_text(encoding="utf-8"))
                for raw in data.get("sources", []):
                    src = PodcastSource.from_dict(raw)
                    self._sources[src.id] = src  # Override or add
                log.debug("Loaded %d podcast sources from %s", len(data.get("sources", [])), self._file)
            except (json.JSONDecodeError, TypeError, KeyError) as exc:
                log.warning("Could not parse podcast sources file %s: %s", self._file, exc)
        else:
            log.debug("No podcast_sources.json found at %s; using built-in defaults", self._file)
            # Write the defaults so the user can edit them
            self._save()

    def _save(self) -> None:
        """Atomically persist the current registry to JSON."""
        self._file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".json.tmp")
        data = {
            "version": 1,
            "sources": [s.to_dict() for s in self._sources.values()],
        }
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(self._file)

    # ------------------------------------------------------------------
    # Read access
    # ------------------------------------------------------------------

    def all(self) -> list[PodcastSource]:
        """Return all registered sources."""
        return list(self._sources.values())

    def enabled(self) -> list[PodcastSource]:
        """Return only enabled sources."""
        return [s for s in self._sources.values() if s.enabled]

    def get(self, source_id: str) -> Optional[PodcastSource]:
        """Look up a source by its slug ID."""
        return self._sources.get(source_id)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add(self, source: PodcastSource, *, save: bool = True) -> None:
        """Add or replace a source; persists by default."""
        self._sources[source.id] = source
        if save:
            self._save()

    def remove(self, source_id: str, *, save: bool = True) -> bool:
        """Remove a source by ID; returns True if found."""
        if source_id not in self._sources:
            return False
        del self._sources[source_id]
        if save:
            self._save()
        return True

    def enable(self, source_id: str, *, save: bool = True) -> bool:
        """Enable a source; returns True if found."""
        src = self._sources.get(source_id)
        if src is None:
            return False
        self._sources[source_id] = PodcastSource(**{**asdict(src), "enabled": True})
        if save:
            self._save()
        return True

    def disable(self, source_id: str, *, save: bool = True) -> bool:
        """Disable a source; returns True if found."""
        src = self._sources.get(source_id)
        if src is None:
            return False
        self._sources[source_id] = PodcastSource(**{**asdict(src), "enabled": False})
        if save:
            self._save()
        return True

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_list(self) -> list[dict]:
        """Return all sources as a list of plain dicts."""
        return [s.to_dict() for s in self._sources.values()]
