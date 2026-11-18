"""Media acquisition for authorized podcast episodes.

STRICT ENFORCEMENT: No video or audio is downloaded unless a valid RightsRecord
with status=AUTHORIZED is confirmed by RightsChecker.
"""
from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .rights import RightsChecker
from .utils import get_ffmpeg_binary, get_ffprobe_binary

log = logging.getLogger(__name__)

# Base storage directory for podcast media
DEFAULT_MEDIA_DIR = Path("data/podcast_media")


@dataclass
class AcquiredMedia:
    """Local media paths for an authorized podcast episode."""

    episode_id: str
    video_path: Path
    audio_path: Path
    duration_seconds: float
    title: str
    thumbnail_path: Optional[Path] = None


class MediaAcquisition:
    """Downloads audio and video for authorized episodes only."""

    def __init__(self, store, base_dir: Optional[Path] = None, cfg=None):
        self.store = store
        self.cfg = cfg
        self.rights_checker = RightsChecker(store)
        self.base_dir = Path(base_dir) if base_dir else DEFAULT_MEDIA_DIR
        self.base_dir.mkdir(parents=True, exist_ok=True)

    def acquire(self, episode_id: str, force_download: bool = False) -> AcquiredMedia:
        """Acquire media for an authorized episode.

        Raises:
            PermissionError: If episode is not authorized by the rights layer.
            RuntimeError: If download or extraction fails.
        """
        # Hard gate: verify rights
        can_proceed, reason = self.rights_checker.check_before_processing(episode_id)
        if not can_proceed:
            raise PermissionError(f"Cannot acquire media: {reason}")

        ep = self.store.get_podcast_episode(episode_id)
        if not ep:
            raise ValueError(f"Episode {episode_id} not found in database")

        video_url = ep.get("video_url")
        if not video_url:
            video_id = ep.get("video_id")
            if not video_id:
                raise ValueError(f"Episode {episode_id} has no video URL or ID")
            video_url = f"https://www.youtube.com/watch?v={video_id}"

        # Safe directory name
        safe_id = episode_id.replace(":", "_").replace("/", "_")
        dest_dir = self.base_dir / safe_id
        dest_dir.mkdir(parents=True, exist_ok=True)

        video_path = dest_dir / "source.mp4"
        audio_path = dest_dir / "audio_16k.wav"

        # Check existing cached media
        if not force_download and video_path.is_file() and audio_path.is_file():
            if video_path.stat().st_size > 1000 and audio_path.stat().st_size > 1000:
                log.info("Using cached media for %s at %s", episode_id, dest_dir)
                dur = self._get_duration(video_path)
                return AcquiredMedia(
                    episode_id=episode_id,
                    video_path=video_path,
                    audio_path=audio_path,
                    duration_seconds=dur,
                    title=ep.get("title", ""),
                )

        log.info("Downloading authorized video for %s from %s", episode_id, video_url)
        self._download_video(video_url, video_path)

        log.info("Extracting 16kHz mono WAV audio from %s", video_path)
        self._extract_audio(video_path, audio_path)

        dur = self._get_duration(video_path)
        return AcquiredMedia(
            episode_id=episode_id,
            video_path=video_path,
            audio_path=audio_path,
            duration_seconds=dur,
            title=ep.get("title", ""),
        )

    def _download_video(self, url: str, target_file: Path) -> None:
        """Download video using yt-dlp to target_file."""
        import yt_dlp

        ffmpeg_bin = get_ffmpeg_binary(self.cfg)
        ydl_opts = {
            "format": "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": str(target_file.parent / f"{target_file.stem}.%(ext)s"),
            "merge_output_format": "mp4",
            "ffmpeg_location": ffmpeg_bin,
            "quiet": True,
            "no_warnings": True,
            "overwrites": True,
        }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
        except Exception:
            pass

        # Verify output file exists; check for alternative container if necessary
        if not target_file.is_file():
            for f in target_file.parent.glob("source*"):
                if f.suffix.lower() in (".mp4", ".mkv", ".webm") and f.stat().st_size > 1000:
                    if f != target_file:
                        if target_file.is_file():
                            target_file.unlink()
                        f.rename(target_file)
                    break

        if not target_file.is_file():
            raise RuntimeError(f"yt-dlp download failed for {url}")

    def _extract_audio(self, video_file: Path, audio_file: Path) -> None:
        """Extract 16kHz mono 16-bit WAV audio for Whisper using FFmpeg."""
        ffmpeg_bin = get_ffmpeg_binary(self.cfg)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-i", str(video_file),
            "-vn",
            "-acodec", "pcm_s16le",
            "-ar", "16000",
            "-ac", "1",
            str(audio_file),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0 or not audio_file.is_file() or audio_file.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg audio extraction failed: {proc.stderr[:500]}")

    def _get_duration(self, media_path: Path) -> float:
        """Get media duration in seconds using ffprobe."""
        ffprobe_bin = get_ffprobe_binary(self.cfg)
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            return float(proc.stdout.strip())
        except Exception:
            return 0.0
