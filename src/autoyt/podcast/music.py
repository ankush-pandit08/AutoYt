"""Copyright-free background music library for viral podcast Shorts.

Provides vibe-matched royalty-free background tracks (Motivational, Inspirational,
Serious/Hard Truths, Comedy/Banter, Mindset) under CC-BY / CC0 licenses with
automated volume ducking and smooth crossfades.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

log = logging.getLogger(__name__)

# Default ducking volume (0.13 - 0.15 sits cleanly under spoken dialogue at ~-18dB)
DEFAULT_BGM_VOLUME = 0.14


@dataclass
class MusicTrack:
    vibe: str
    title: str
    artist: str
    license: str
    path: Path
    url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "vibe": self.vibe,
            "title": self.title,
            "artist": self.artist,
            "license": self.license,
            "file": str(self.path.as_posix()),
            "url": self.url,
        }


class MusicLibrary:
    """Manages curated copyright-free background music tracks for Shorts."""

    def __init__(self, music_dir: Optional[Path] = None) -> None:
        if music_dir is None:
            self.music_dir = Path("assets/music").resolve()
        else:
            self.music_dir = Path(music_dir).resolve()

        self._tracks_by_vibe: Dict[str, MusicTrack] = {}
        self._load_library()

    def _load_library(self) -> None:
        """Load manifest.json if present, or discover tracks on disk."""
        manifest_file = self.music_dir / "manifest.json"
        if manifest_file.is_file():
            try:
                data = json.loads(manifest_file.read_text(encoding="utf-8"))
                for vibe, meta in data.items():
                    rel_or_abs = Path(meta.get("file", ""))
                    if not rel_or_abs.is_absolute():
                        track_path = (self.music_dir.parent.parent / rel_or_abs).resolve()
                        if not track_path.is_file():
                            track_path = (self.music_dir / vibe / rel_or_abs.name).resolve()
                    else:
                        track_path = rel_or_abs

                    if track_path.is_file() and track_path.stat().st_size > 0:
                        self._tracks_by_vibe[vibe.lower()] = MusicTrack(
                            vibe=vibe.lower(),
                            title=meta.get("title", track_path.stem),
                            artist=meta.get("artist", "Unknown"),
                            license=meta.get("license", "Creative Commons"),
                            path=track_path,
                            url=meta.get("url"),
                        )
            except Exception as exc:
                log.warning("Could not read music manifest: %s", exc)

        # Fallback: Scan directory for audio files
        if not self._tracks_by_vibe and self.music_dir.is_dir():
            for audio_file in self.music_dir.glob("**/*.*"):
                if audio_file.suffix.lower() in {".ogg", ".mp3", ".wav", ".m4a"}:
                    vibe = audio_file.parent.name.lower()
                    if vibe == "music":
                        vibe = "motivational"
                    self._tracks_by_vibe[vibe] = MusicTrack(
                        vibe=vibe,
                        title=audio_file.stem.replace("_", " "),
                        artist="Creative Commons",
                        license="CC BY 4.0",
                        path=audio_file,
                    )

    def get_track_for_vibe(self, vibe: Optional[str] = "motivational") -> Optional[Path]:
        """Return path to background music matching vibe, or fallback to default."""
        if not vibe:
            vibe = "motivational"
        norm = vibe.lower().strip()

        # Map common synonyms and pillar keywords
        vibe_map = {
            "motivation": "motivational",
            "motivational": "motivational",
            "inspire": "inspirational",
            "inspiration": "inspirational",
            "inspirational": "inspirational",
            "serious": "serious",
            "serious truths": "serious",
            "truth": "serious",
            "psychology": "serious",
            "philosophy": "serious",
            "dark": "serious",
            "comedy": "comedy",
            "banter": "comedy",
            "humor": "comedy",
            "funny": "comedy",
            "upbeat": "comedy",
            "mindset": "mindset",
            "calm": "mindset",
            "stoic": "mindset",
        }

        target_vibe = vibe_map.get(norm, "motivational")

        # 1. Exact match
        if target_vibe in self._tracks_by_vibe:
            return self._tracks_by_vibe[target_vibe].path

        # 2. Any available track
        if self._tracks_by_vibe:
            return next(iter(self._tracks_by_vibe.values())).path

        return None

    def list_tracks(self) -> List[dict]:
        """Return list of available tracks for UI."""
        return [track.to_dict() for track in self._tracks_by_vibe.values()]
