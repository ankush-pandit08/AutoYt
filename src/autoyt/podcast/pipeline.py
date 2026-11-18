"""End-to-end Podcast Clipping Pipeline.

Coordinates:
  Rights Check -> Media Acquisition -> Transcription -> Clip Finding ->
  Subtitles -> 9:16 Video Rendering -> Thumbnails -> Metadata -> Review Queue.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from .acquisition import MediaAcquisition
from .clip_editor import ClipEditor
from .clip_finder import ClipFinder
from .metadata import MetadataGenerator
from .rights import RightsChecker
from .sources import SourceRegistry
from .subtitles import SubtitleGenerator
from .thumbnail import ThumbnailGenerator
from .transcription import Transcriber

log = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Path("output/podcast")


@dataclass
class ProcessedClipResult:
    clip_id: str
    video_path: Path
    thumbnail_path: Path
    title: str
    description: str
    score: float
    duration: float


class PodcastPipeline:
    """Orchestrates extraction of YouTube Shorts from an authorized podcast episode."""

    def __init__(self, cfg, store, output_dir: Optional[Path] = None):
        self.cfg = cfg
        self.store = store
        self.output_dir = Path(output_dir) if output_dir else DEFAULT_OUTPUT_DIR
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.registry = SourceRegistry()
        self.rights_checker = RightsChecker(store)
        self.acquisition = MediaAcquisition(store, cfg=cfg)
        self.transcriber = Transcriber(
            model_size=getattr(cfg, "whisper_model", "base"),
            device="cpu",
            compute_type="int8",
        )
        self.clip_finder = ClipFinder(
            min_duration=40.0,
            max_duration=60.0,
            target_duration=55.0,
            gemini_api_key=getattr(cfg, "gemini_api_key", None),
        )
        self.subtitle_gen = SubtitleGenerator(style="dynamic")
        self.clip_editor = ClipEditor(cfg=cfg)
        self.thumb_gen = ThumbnailGenerator(cfg=cfg)
        self.meta_gen = MetadataGenerator()

    def process_episode(
        self,
        episode_id: str,
        max_clips: int = 4,
        subtitle_style: str = "dynamic",
        bgm_vibe: Optional[str] = None,
        enable_bgm: bool = True,
    ) -> List[ProcessedClipResult]:
        """Run full clipping pipeline for an authorized episode.

        Raises:
            PermissionError: If episode is not authorized by the rights layer.
        """
        log.info("Starting podcast pipeline for episode %s (bgm_vibe=%s, enable_bgm=%s)", episode_id, bgm_vibe, enable_bgm)

        # 1. HARD GATE: Rights verification
        can_proceed, reason = self.rights_checker.check_before_processing(episode_id)
        if not can_proceed:
            self.store.update_episode_status(episode_id, "skipped", error_message=reason)
            raise PermissionError(f"Rights gate blocked processing: {reason}")

        ep = self.store.get_podcast_episode(episode_id)
        if not ep:
            raise ValueError(f"Episode {episode_id} not found in database")

        source = self.registry.get(ep["source_id"])
        if not source:
            raise ValueError(f"Source {ep['source_id']} not found in registry")

        self.store.update_episode_status(episode_id, "processing")

        try:
            # 2. Acquire media
            media = self.acquisition.acquire(episode_id)

            # 3. Transcribe audio with word timestamps
            cache_file = media.audio_path.parent / "transcript.json"
            transcript = self.transcriber.transcribe(
                media.audio_path, episode_id=episode_id, cache_file=cache_file
            )

            # 4. Discover candidate clips
            candidates = self.clip_finder.find_clips(
                transcript, source_id=ep["source_id"], max_clips=max_clips
            )
            if not candidates:
                log.warning("No suitable 40-60s candidate clips found in %s", episode_id)
                self.store.update_episode_status(episode_id, "processed")
                return []

            # 5. Process and render each candidate clip
            results: List[ProcessedClipResult] = []
            safe_ep_id = episode_id.replace(":", "_").replace("/", "_")
            ep_out_dir = self.output_dir / safe_ep_id
            ep_out_dir.mkdir(parents=True, exist_ok=True)

            for cand in candidates:
                existing_clip = self.store.get_podcast_clip(cand.id)
                if existing_clip and existing_clip.get("status") in ("uploaded", "approved"):
                    log.info("Clip %s already exists with status '%s'; preserving.", cand.id, existing_clip.get("status"))
                    results.append(
                        ProcessedClipResult(
                            clip_id=cand.id,
                            video_path=Path(existing_clip["output_path"]) if existing_clip.get("output_path") else ep_out_dir,
                            thumbnail_path=Path(existing_clip["thumbnail_path"]) if existing_clip.get("thumbnail_path") else ep_out_dir,
                            title=existing_clip.get("generated_title") or cand.suggested_title,
                            description=existing_clip.get("generated_description") or cand.suggested_description,
                            score=existing_clip.get("ai_score") or cand.ai_score,
                            duration=existing_clip.get("duration_seconds") or cand.duration_seconds,
                        )
                    )
                    continue

                clip_out_dir = ep_out_dir / f"clip_{cand.clip_index}"
                clip_out_dir.mkdir(parents=True, exist_ok=True)

                # Subtitles
                ass_path = clip_out_dir / "subtitles.ass"
                self.subtitle_gen.generate_ass(
                    cand.words,
                    clip_start=cand.start_seconds,
                    output_file=ass_path,
                    style=subtitle_style,
                )

                # Vibe for background music matching
                cat = cand.score_breakdown.get("category", "Motivational")
                resolved_vibe = bgm_vibe if bgm_vibe and bgm_vibe != "auto" else cat.lower()

                # Render vertical video with vibe-matched background music
                video_out_path = clip_out_dir / "final.mp4"
                self.clip_editor.render_clip(
                    source_video=media.video_path,
                    start_seconds=cand.start_seconds,
                    duration_seconds=cand.duration_seconds,
                    subtitles_ass=ass_path,
                    output_file=video_out_path,
                    vibe=resolved_vibe,
                    enable_bgm=enable_bgm,
                )

                # Generate thumbnail
                thumb_out_path = clip_out_dir / "thumbnail.png"
                self.thumb_gen.generate_thumbnail(
                    video_path=video_out_path,
                    timestamp_seconds=min(3.0, cand.duration_seconds / 2),
                    headline=cand.suggested_title,
                    output_file=thumb_out_path,
                )

                # Generate full metadata
                music_credit = None
                if enable_bgm:
                    track_meta = self.clip_editor.music_lib._tracks_by_vibe.get(resolved_vibe)
                    if track_meta:
                        music_credit = f"{track_meta.title} by {track_meta.artist} ({track_meta.license})"

                metadata = self.meta_gen.generate(
                    clip_title=cand.suggested_title,
                    clip_summary=cand.suggested_description,
                    source=source,
                    episode_title=ep.get("title", ""),
                    episode_url=ep.get("video_url", ""),
                    hashtags=cand.suggested_hashtags,
                    rights_basis=ep.get("rights_basis"),
                    music_credit=music_credit,
                )

                # Save clip record in database
                clip_record = {
                    "id": cand.id,
                    "episode_id": episode_id,
                    "source_id": cand.source_id,
                    "clip_index": cand.clip_index,
                    "start_seconds": cand.start_seconds,
                    "end_seconds": cand.end_seconds,
                    "duration_seconds": cand.duration_seconds,
                    "transcript_text": cand.transcript_text,
                    "ai_score": cand.ai_score,
                    "score_breakdown": cand.score_breakdown,
                    "generated_title": metadata.title,
                    "generated_description": metadata.description,
                    "hashtags": metadata.hashtags,
                    "status": "candidate",  # Awaiting manual review
                    "output_path": str(video_out_path.resolve()),
                    "thumbnail_path": str(thumb_out_path.resolve()),
                    "youtube_video_id": None,
                    "youtube_url": None,
                    "upload_status": "pending_review",
                    "review_notes": None,
                }
                self.store.save_podcast_clip(clip_record)

                results.append(
                    ProcessedClipResult(
                        clip_id=cand.id,
                        video_path=video_out_path,
                        thumbnail_path=thumb_out_path,
                        title=metadata.title,
                        description=metadata.description,
                        score=cand.ai_score,
                        duration=cand.duration_seconds,
                    )
                )

            self.store.update_episode_status(episode_id, "processed")
            log.info("Finished processing episode %s: generated %d clips", episode_id, len(results))
            return results

        except Exception as exc:
            log.error("Pipeline failure for episode %s: %s", episode_id, exc, exc_info=True)
            self.store.update_episode_status(episode_id, "failed", error_message=str(exc))
            raise
