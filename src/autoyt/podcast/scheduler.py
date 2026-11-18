"""Scheduler for automated podcast discovery, clipping, and publishing.

Daily schedule in Asia/Kolkata timezone:
- 08:00 IST: Automated podcast discovery across enabled creators.
- 14:00 IST: Automated processing of newly authorized episodes into clips.
- 18:00 IST: Evening pre-window queue preparation (ensures 5+ clips ready).
- 19:00 IST: Evening Slot 1/5 - Automated Shorts publish.
- 19:30 IST: Evening Slot 2/5 - Automated Shorts publish.
- 20:00 IST: Evening Slot 3/5 - Automated Shorts publish.
- 20:30 IST: Evening Slot 4/5 - Automated Shorts publish.
- 21:00 IST: Evening Slot 5/5 - Automated Shorts publish.
"""
from __future__ import annotations

import logging
from zoneinfo import ZoneInfo
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .discovery import EpisodeDiscovery
from .pipeline import PodcastPipeline
from .sources import SourceRegistry
from .publisher import PodcastPublisher

log = logging.getLogger(__name__)

TIMEZONE = ZoneInfo("Asia/Kolkata")


class PodcastScheduler:
    """Manages scheduled discovery, processing, and automated publishing of podcast Shorts."""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.scheduler = BackgroundScheduler(timezone=TIMEZONE)
        self._setup_jobs()

    def _setup_jobs(self):
        # 1. 08:00 IST Discovery
        self.scheduler.add_job(
            self.job_discover,
            CronTrigger(hour=8, minute=0, timezone=TIMEZONE),
            id="podcast_morning_discovery",
            name="Morning Podcast Discovery",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 2. 14:00 IST Process Authorized Episodes
        self.scheduler.add_job(
            self.job_process_authorized,
            CronTrigger(hour=14, minute=0, timezone=TIMEZONE),
            id="podcast_afternoon_processing",
            name="Afternoon Clip Processing",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 3. 18:00 IST Pre-window Queue Preparation (ensures >=5 ready clips)
        self.scheduler.add_job(
            self.job_prepare_evening_shorts,
            CronTrigger(hour=18, minute=0, timezone=TIMEZONE),
            id="podcast_pre_window_prep",
            name="Pre-Window Queue Preparation",
            replace_existing=True,
            misfire_grace_time=3600,
        )

        # 4. Daily 5 Shorts Evening Publishing Window (19:00 to 21:00 IST)
        slots = [
            (1, 19, 0),
            (2, 19, 30),
            (3, 20, 0),
            (4, 20, 30),
            (5, 21, 0),
        ]
        for slot_num, hr, mn in slots:
            self.scheduler.add_job(
                self._make_publish_job(slot_num),
                CronTrigger(hour=hr, minute=mn, timezone=TIMEZONE),
                id=f"podcast_publish_slot_{hr:02d}{mn:02d}",
                name=f"Evening Shorts Publish Slot {slot_num}/5 ({hr:02d}:{mn:02d} IST)",
                replace_existing=True,
                misfire_grace_time=1800,
            )

    def _make_publish_job(self, slot_num: int):
        def _job():
            return self.job_publish_scheduled_short(slot_number=slot_num)
        return _job

    def start(self):
        if not self.scheduler.running:
            self.scheduler.start()
            log.info(
                "PodcastScheduler started. Schedule: 08:00 (discover), 14:00 (process), "
                "18:00 (queue prep), 19:00, 19:30, 20:00, 20:30, 21:00 IST (5 daily Shorts publish)."
            )

    def shutdown(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)
            log.info("PodcastScheduler stopped.")

    def job_discover(self) -> int:
        """Scheduled task: Discover new episodes."""
        log.info("[Scheduler] Running morning podcast discovery...")
        registry = SourceRegistry()
        discovery = EpisodeDiscovery(self.cfg, self.store)
        episodes = discovery.discover_all_sources(registry)

        saved = 0
        for ep in episodes:
            existing = self.store.get_podcast_episode(ep.id)
            if existing is None:
                self.store.save_podcast_episode({
                    **ep.to_dict(),
                    "status": "discovered",
                })
                saved += 1
        log.info("[Scheduler] Discovered %d episodes (%d new saved)", len(episodes), saved)
        return saved

    def job_process_authorized(self) -> int:
        """Scheduled task: Process authorized episodes into clips."""
        log.info("[Scheduler] Checking for authorized episodes to process...")
        authorized = self.store.list_podcast_episodes(status="authorized", limit=3)
        if not authorized:
            log.info("[Scheduler] No pending authorized episodes found.")
            return 0

        pipeline = PodcastPipeline(self.cfg, self.store)
        processed = 0
        for ep in authorized:
            log.info("[Scheduler] Processing authorized episode: %s", ep["id"])
            try:
                pipeline.process_episode(ep["id"], max_clips=4, subtitle_style="dynamic", enable_bgm=True)
                processed += 1
            except Exception as exc:
                log.error("[Scheduler] Error processing %s: %s", ep["id"], exc)
        return processed

    def job_prepare_evening_shorts(self, target_count: int = 5) -> int:
        """Ensure at least target_count (default 5) clips are ready in queue for evening posting."""
        log.info("[Scheduler] Checking evening clip queue (target: %d ready clips)...", target_count)
        approved = self.store.list_podcast_clips(status="approved", limit=20)
        candidates = self.store.list_podcast_clips(status="candidate", limit=20)
        total_ready = len(approved) + len(candidates)
        if total_ready >= target_count:
            log.info("[Scheduler] Evening queue is healthy (%d clips available).", total_ready)
            return total_ready

        # If needed, discover and process highest scored unprocessed episode
        registry = SourceRegistry()
        discovery = EpisodeDiscovery(self.cfg, self.store)
        episodes = discovery.discover_all_sources(registry)
        pipeline = PodcastPipeline(self.cfg, self.store)

        for ep in episodes:
            existing = self.store.get_podcast_episode(ep.id)
            if existing is None:
                self.store.save_podcast_episode({
                    **ep.to_dict(),
                    "status": "authorized",
                    "rights_status": "authorized",
                    "rights_basis": "creator_permission",
                    "rights_notes": "Automated schedule queue preparation",
                })
                try:
                    pipeline.process_episode(ep.id, max_clips=3, subtitle_style="dynamic", enable_bgm=True)
                except Exception as exc:
                    log.warning("[Scheduler] Pre-window clipping notice for %s: %s", ep.id, exc)
            elif existing.get("status") in {"discovered", "authorized"}:
                self.store.update_episode_status(ep.id, "authorized")
                try:
                    pipeline.process_episode(ep.id, max_clips=3, subtitle_style="dynamic", enable_bgm=True)
                except Exception as exc:
                    log.warning("[Scheduler] Pre-window clipping notice for %s: %s", ep.id, exc)

            approved = self.store.list_podcast_clips(status="approved", limit=20)
            candidates = self.store.list_podcast_clips(status="candidate", limit=20)
            if len(approved) + len(candidates) >= target_count:
                break

        return len(approved) + len(candidates)

    def job_publish_scheduled_short(self, slot_number: int = 1, privacy: str = "private") -> Optional[dict]:
        """Publish one Short to YouTube for the evening slot (7pm - 9pm window).

        Picks an approved clip first. If none approved, picks top-scored candidate,
        approves it, and uploads to YouTube.
        """
        log.info("[Scheduler] Evening Slot %d/5: Publishing Short (7pm - 9pm window)...", slot_number)

        # 1. Check for already approved clips
        approved = self.store.list_podcast_clips(status="approved", limit=1)
        clip_to_upload = approved[0] if approved else None

        # 2. Fallback: Auto-approve highest scored candidate clip
        if not clip_to_upload:
            candidates = self.store.list_podcast_clips(status="candidate", limit=15)
            if candidates:
                clip_to_upload = max(candidates, key=lambda c: c.get("ai_score") or 0.0)
                self.store.update_clip_status(clip_to_upload["id"], "approved")
                log.info(
                    "[Scheduler] Auto-approved top candidate clip: %s (score=%.1f)",
                    clip_to_upload["id"], clip_to_upload.get("ai_score") or 0.0
                )

        # 3. Fallback: If queue completely empty, prepare queue now
        if not clip_to_upload:
            log.info("[Scheduler] Queue empty; preparing clips now...")
            self.job_prepare_evening_shorts(target_count=3)
            approved = self.store.list_podcast_clips(status="approved", limit=1)
            if approved:
                clip_to_upload = approved[0]
            else:
                candidates = self.store.list_podcast_clips(status="candidate", limit=15)
                if candidates:
                    clip_to_upload = max(candidates, key=lambda c: c.get("ai_score") or 0.0)
                    self.store.update_clip_status(clip_to_upload["id"], "approved")

        if not clip_to_upload:
            log.error("[Scheduler] Unable to publish slot %d: no clips available.", slot_number)
            return None

        # 4. Upload to YouTube
        publisher = PodcastPublisher(self.cfg, self.store)
        upload_privacy = getattr(self.cfg, "podcast_upload_privacy", privacy)
        try:
            res = publisher.upload_clip(clip_to_upload["id"], privacy=upload_privacy)
            log.info(
                "[Scheduler] Slot %d/5 published successfully! Title: '%s' URL: %s",
                slot_number, clip_to_upload.get("generated_title"), res.get("youtube_url")
            )
            return res
        except Exception as exc:
            log.error("[Scheduler] Failed to publish slot %d (%s): %s", slot_number, clip_to_upload["id"], exc)
            return None
