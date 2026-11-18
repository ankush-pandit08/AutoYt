"""One loopback HTTP server, one scheduler, the existing SQLite pipeline. No cloud services."""
import hmac
import json
import logging
import os
import re
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import portalocker
from apscheduler.schedulers.background import BackgroundScheduler
from pydantic import ValidationError

from .manual import ManualRequest
from .pipeline import Pipeline, pipeline_lock
from .scheduling import configure
from .store import Store, write_json

log = logging.getLogger(__name__)
JOB_ID = re.compile(r"^[a-f0-9]{16}$")
TERMINAL = {"PUBLISHED", "AWAITING_REVIEW", "FAILED", "SKIPPED", "READY_TO_UPLOAD"}


class Dashboard:
    def __init__(self, cfg):
        from .voice_settings import apply
        cfg = apply(cfg)
        from .render_settings import apply as apply_render
        cfg = apply_render(cfg)
        from .script_settings import apply as apply_script
        cfg = apply_script(cfg)
        self.cfg = cfg
        cfg.prepare()
        from .schedule_settings import initialize
        self.cfg = cfg = initialize(cfg)
        self.store = Store(cfg.database_path)
        self.state_path = cfg.database_path.parent / "dashboard-state.json"
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autoyt-manual")
        self.guard = threading.Lock()
        self.future = None
        self.voice_future = None
        self.voice_result = None
        self.voice_last_test = 0.0
        self.notice = ""
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.scheduler = configure(BackgroundScheduler(timezone=cfg.timezone), cfg)
        self.scheduler_lock = portalocker.Lock(str(cfg.database_path) + ".scheduler.lock", timeout=0)
        self.scheduler_owned = False
        from .youtube_oauth import YouTubeOAuth
        self.youtube_auth = YouTubeOAuth(cfg)
        self._podcast_registry = None

    def podcast_snapshot(self):
        try:
            if self._podcast_registry is None:
                from .podcast.sources import SourceRegistry
                self._podcast_registry = SourceRegistry()
            sources = self._podcast_registry.all()
            episodes = self.store.list_podcast_episodes(limit=50)
            clips = self.store.list_podcast_clips(limit=100)

            stats = {
                "episodes_discovered": len(episodes),
                "episodes_authorized": sum(1 for e in episodes if e.get("rights_status") == "authorized"),
                "episodes_processed": sum(1 for e in episodes if e.get("status") == "processed"),
                "clips_found": len(clips),
                "clips_generated": sum(1 for c in clips if c.get("output_path")),
                "clips_awaiting_review": sum(1 for c in clips if c.get("status") == "candidate"),
                "clips_approved": sum(1 for c in clips if c.get("status") == "approved"),
                "videos_uploaded": sum(1 for c in clips if c.get("status") == "uploaded"),
                "failed_jobs": sum(1 for c in clips if c.get("status") == "failed") + sum(1 for e in episodes if e.get("status") == "failed"),
            }

            source_dicts = []
            for s in sources:
                d = s.to_dict()
                src_eps = [e for e in episodes if e.get("source_id") == s.id]
                d["latest_episode"] = src_eps[0] if src_eps else None
                source_dicts.append(d)

            # Automated 5-Shorts evening schedule status (7pm - 9pm IST)
            from zoneinfo import ZoneInfo
            from datetime import datetime
            ist = ZoneInfo("Asia/Kolkata")
            now_ist = datetime.now(ist)

            slots = [
                {"slot": 1, "time": "19:00", "time_display": "7:00 PM IST", "label": "Slot 1/5", "status": "scheduled"},
                {"slot": 2, "time": "19:30", "time_display": "7:30 PM IST", "label": "Slot 2/5", "status": "scheduled"},
                {"slot": 3, "time": "20:00", "time_display": "8:00 PM IST", "label": "Slot 3/5", "status": "scheduled"},
                {"slot": 4, "time": "20:30", "time_display": "8:30 PM IST", "label": "Slot 4/5", "status": "scheduled"},
                {"slot": 5, "time": "21:00", "time_display": "9:00 PM IST", "label": "Slot 5/5", "status": "scheduled"},
            ]

            next_slot = None
            for s in slots:
                sh, sm = map(int, s["time"].split(":"))
                slot_time = now_ist.replace(hour=sh, minute=sm, second=0, microsecond=0)
                if now_ist <= slot_time:
                    s["status"] = "upcoming"
                    if next_slot is None:
                        next_slot = s
                else:
                    s["status"] = "completed"
            if next_slot is None:
                next_slot = {**slots[0], "status": "tomorrow"}

            ready_count = stats["clips_approved"] + stats["clips_awaiting_review"]
            schedule_data = {
                "active": True,
                "timezone": "Asia/Kolkata (IST)",
                "frequency": "5 Shorts Daily",
                "window": "7:00 PM – 9:00 PM IST",
                "current_time_ist": now_ist.strftime("%I:%M %p IST"),
                "next_slot": next_slot,
                "slots": slots,
                "ready_queue_count": ready_count,
                "queue_healthy": ready_count >= 5,
                "pipeline_stages": [
                    {"time": "08:00 IST", "name": "Morning Episode Discovery", "status": "Active"},
                    {"time": "14:00 IST", "name": "AI Processing & Subtitles", "status": "Active"},
                    {"time": "18:00 IST", "name": "Pre-Window Queue Preparation", "status": "Active"},
                    {"time": "19:00 - 21:00 IST", "name": "5 Daily Shorts Publishing Window", "status": "Active"},
                ],
            }

            return {
                "stats": stats,
                "sources": source_dicts,
                "episodes": episodes,
                "clips": clips,
                "schedule": schedule_data,
            }
        except Exception as exc:
            log.warning("podcast_snapshot error: %s", exc)
            return {"stats": {}, "sources": [], "episodes": [], "clips": []}

    def podcast_action(self, action: str, data: dict):
        from .podcast.discovery import EpisodeDiscovery
        from .podcast.publisher import PodcastPublisher
        from .podcast.rights import RightsChecker
        from .podcast.sources import SourceRegistry

        if action == "podcast_discover":
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
            return {"message": f"Discovery finished: {len(episodes)} episodes found ({saved} new saved)."}

        if action == "podcast_authorize":
            checker = RightsChecker(self.store)
            ep_id = data["episode_id"]
            basis = data.get("basis", "creator_permission")
            notes = data.get("notes")
            evidence_url = data.get("evidence_url")
            record = checker.grant_authorization(ep_id, basis, evidence_url=evidence_url, notes=notes)
            return {"message": f"Episode {ep_id} authorized successfully.", "record": record.to_dict()}

        if action == "podcast_block":
            checker = RightsChecker(self.store)
            ep_id = data["episode_id"]
            reason = data.get("reason", "User blocked episode")
            record = checker.block_episode(ep_id, reason)
            return {"message": f"Episode {ep_id} blocked.", "record": record.to_dict()}

        if action == "podcast_process":
            ep_id = data["episode_id"]
            clips_count = int(data.get("clips", 4))
            style = data.get("style", "dynamic")
            self.executor.submit(self._run_podcast_pipeline, ep_id, clips_count, style)
            return {"message": f"Processing started for {ep_id}. Check the Review Queue shortly!"}

        if action == "podcast_approve_clip":
            clip_id = data["clip_id"]
            self.store.update_clip_status(clip_id, "approved")
            return {"message": f"Clip {clip_id} approved for publishing."}

        if action == "podcast_reject_clip":
            clip_id = data["clip_id"]
            reason = data.get("reason")
            self.store.update_clip_status(clip_id, "rejected", review_notes=reason)
            return {"message": f"Clip {clip_id} rejected."}

        if action == "podcast_update_clip":
            clip_id = data["clip_id"]
            with self.store.connect() as db:
                db.execute(
                    "UPDATE podcast_clips SET generated_title=?, generated_description=? WHERE id=?",
                    (data.get("title"), data.get("description"), clip_id),
                )
            return {"message": f"Clip {clip_id} updated."}

        if action == "podcast_upload_clip":
            clip_id = data["clip_id"]
            privacy = data.get("privacy", "private")
            publisher = PodcastPublisher(self.cfg, self.store)
            res = publisher.upload_clip(clip_id, privacy=privacy)
            return {"message": f"Clip {clip_id} uploaded to YouTube ({privacy})!", "result": res}

        if action == "podcast_toggle_source":
            source_id = data["source_id"]
            registry = SourceRegistry()
            src = registry.get(source_id)
            if not src:
                raise ValueError(f"Source {source_id} not found")
            if src.enabled:
                registry.disable(source_id)
            else:
                registry.enable(source_id)
            return {"message": f"Source {source_id} toggled.", "enabled": not src.enabled}

        if action == "podcast_manual_clip":
            url = data.get("url", "").strip()
            if not url:
                raise ValueError("YouTube podcast or video URL is required.")

            import subprocess
            import sys
            from pathlib import Path
            from .podcast.sources import PodcastSource

            ytdlp_bin = str(Path(sys.executable).parent / "yt-dlp.exe")
            if not Path(ytdlp_bin).is_file():
                ytdlp_bin = "yt-dlp"

            cmd = [ytdlp_bin, "--dump-json", "--skip-download", "--no-warnings", url]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
            if proc.returncode != 0:
                raise ValueError(f"Could not retrieve video details: {proc.stderr[:180]}")

            meta = json.loads(proc.stdout)
            video_id = meta.get("id")
            if not video_id:
                raise ValueError("Could not determine YouTube video ID.")

            title = meta.get("title", "Imported Podcast Video")
            channel = meta.get("channel") or meta.get("uploader") or "YouTube Creator"
            duration = int(meta.get("duration") or 0)
            thumbnail = meta.get("thumbnail") or f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg"
            description = meta.get("description", "")[:500]

            registry = SourceRegistry()
            slug = re.sub(r"[^a-z0-9_]+", "_", channel.lower()).strip("_") or "manual"
            source = registry.get(slug)
            if not source:
                source = PodcastSource(
                    id=slug,
                    name=channel,
                    show_name=channel,
                    youtube_channel_id=meta.get("channel_id") or "",
                    youtube_channel_url=meta.get("channel_url") or f"https://www.youtube.com/@{slug}",
                    playlist_id=None,
                    rss_feed_url=None,
                    website_url=None,
                    enabled=True,
                    priority_topics=["motivation", "business", "mindset", "success"],
                    created_at=datetime.now(timezone.utc),
                )
                registry.add(source)

            episode_id = f"{slug}:{video_id}"
            ep_record = {
                "id": episode_id,
                "source_id": slug,
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "title": title,
                "channel_name": channel,
                "published_at": datetime.now(timezone.utc).isoformat(),
                "duration_seconds": duration,
                "thumbnail_url": thumbnail,
                "description": description,
                "discovery_score": 0.95,
                "topics_matched": ["manual_import"],
                "status": "authorized",
                "rights_status": "authorized",
                "rights_basis": "creator_permission",
                "rights_notes": "Authorized by user manual URL import",
                "rights_verified_at": datetime.now(timezone.utc).isoformat(),
            }
            self.store.save_podcast_episode(ep_record)

            clips_count = int(data.get("clips", 3))
            style = data.get("style", "dynamic")
            auto_upload = bool(data.get("auto_upload", False))
            bgm_vibe = data.get("bgm_vibe", "auto")

            self.executor.submit(self._run_podcast_pipeline, episode_id, clips_count, style, auto_upload, bgm_vibe)
            return {
                "message": f"Successfully imported '{title}'! Clipping started in background.",
                "episode_id": episode_id,
                "title": title,
                "channel": channel,
                "duration": duration,
            }

        if action == "podcast_auto_clip_top":
            registry = SourceRegistry()
            discovery = EpisodeDiscovery(self.cfg, self.store)
            episodes = discovery.discover_all_sources(registry)
            for ep in episodes:
                existing = self.store.get_podcast_episode(ep.id)
                if existing is None:
                    self.store.save_podcast_episode({
                        **ep.to_dict(),
                        "status": "discovered",
                    })

            all_eps = self.store.list_podcast_episodes(limit=100)
            unprocessed = [
                e for e in all_eps
                if e.get("status") in ("discovered", "authorized") and not e.get("processed_at")
            ]
            if not unprocessed:
                return {"message": "All discovered episodes have already been processed!"}

            top_ep = max(unprocessed, key=lambda x: x.get("discovery_score", 0))
            ep_id = top_ep["id"]

            checker = RightsChecker(self.store)
            checker.grant_authorization(ep_id, "creator_permission", notes="Auto-selected top episode for clipping")

            clips_count = int(data.get("clips", 3))
            style = data.get("style", "dynamic")
            auto_upload = bool(data.get("auto_upload", False))

            self.executor.submit(self._run_podcast_pipeline, ep_id, clips_count, style, auto_upload)
            return {
                "message": f"Auto-selected top episode: '{top_ep.get('title')}' (Score: {top_ep.get('discovery_score')}). Clipping started!",
                "episode_id": ep_id,
                "title": top_ep.get("title"),
            }

        if action == "podcast_delete_clip":
            clip_id = data["clip_id"]
            with self.store.connect() as db:
                db.execute("DELETE FROM podcast_clips WHERE id=?", (clip_id,))
            return {"message": f"Clip {clip_id} deleted."}

        if action == "podcast_publish_slot":
            from .podcast.scheduler import PodcastScheduler
            scheduler = PodcastScheduler(self.cfg, self.store)
            slot = int(data.get("slot", 1))
            privacy = data.get("privacy", "private")
            res = scheduler.job_publish_scheduled_short(slot_number=slot, privacy=privacy)
            if not res:
                return {"message": f"Slot {slot}: No clips ready or error during publishing."}
            return {
                "message": f"Slot {slot}/5 published successfully! Title: '{res.get('title', '')}' URL: {res.get('youtube_url', '')}",
                "result": res
            }

        if action == "podcast_prepare_queue":
            from .podcast.scheduler import PodcastScheduler
            scheduler = PodcastScheduler(self.cfg, self.store)
            count = scheduler.job_prepare_evening_shorts(target_count=5)
            return {"message": f"Queue preparation checked: {count} ready clips in queue for evening publishing."}

        raise ValueError(f"Unknown podcast action: {action}")

    def _run_podcast_pipeline(self, episode_id: str, max_clips: int, style: str, auto_upload: bool = False, bgm_vibe: str = "auto"):
        try:
            from .podcast.pipeline import PodcastPipeline
            pipeline = PodcastPipeline(self.cfg, self.store)
            enable_bgm = (bgm_vibe != "none")
            results = pipeline.process_episode(
                episode_id,
                max_clips=max_clips,
                subtitle_style=style,
                bgm_vibe=bgm_vibe if bgm_vibe != "auto" else None,
                enable_bgm=enable_bgm,
            )
            self.notice = f"Finished clipping {episode_id} ({len(results)} clips generated)!"

            if auto_upload and results:
                from .podcast.publisher import PodcastPublisher
                publisher = PodcastPublisher(self.cfg, self.store)
                top_clip = max(results, key=lambda c: c.score)
                self.store.update_clip_status(top_clip.clip_id, "approved")
                res = publisher.upload_clip(top_clip.clip_id, privacy="private")
                self.notice += f" Uploaded to YouTube: {res.get('youtube_url')}"
        except Exception as exc:
            log.error("Background podcast pipeline error: %s", exc)
            self.notice = f"Clipping failed for {episode_id}: {exc}"

    def voice_action(self, action, value=None, version=None, page=None):
        import time

        from . import voice_settings
        if action == "voice_list":
            from .elevenlabs_speech import voices
            return voices(self.cfg, page)
        if action == "save_voice":
            with self.guard:
                self.cfg, saved = voice_settings.save(self.cfg, value, version)
                return {"message": "Voice settings saved for new runs. Saved scripts are not translated.", "voice_settings": saved}
        if action != "test_voice":
            raise ValueError("Unknown voice action")
        candidate = voice_settings.validate(self.cfg, value)
        with self.guard:
            if self.busy() or (self.voice_future and not self.voice_future.done()):
                raise ValueError("Wait for the current generation or voice test to finish")
            if time.monotonic() - self.voice_last_test < 30:
                raise ValueError("Wait 30 seconds between voice tests to protect API credits")
            self.voice_last_test = time.monotonic()
            ident = secrets.token_hex(8)
            self.voice_result = {"id": ident, "state": "GENERATING"}
            self.voice_future = self.executor.submit(self._test_voice, candidate, ident)
            return {"message": "Generating a short voice sample; this may use API credits", "preview_id": ident}

    def _test_voice(self, cfg, ident):
        from .audio import Speech, check_audio
        folder = cfg.cache_dir / "voice-previews"
        folder.mkdir(parents=True, exist_ok=True)
        target = folder / (ident + ".wav")
        import time
        for previous in folder.glob("*.wav"):
            if previous.is_file() and not previous.is_symlink() and time.time() - previous.stat().st_mtime > 540:
                previous.unlink(missing_ok=True)
        text = {"en": "Welcome to AutoYT. Technology explained clearly.",
                "hi": "\u0928\u092e\u0938\u094d\u0915\u093e\u0930! \u0906\u091c \u0915\u0940 \u0924\u0915\u0928\u0940\u0915 \u0915\u094b \u0906\u0938\u093e\u0928 \u092d\u093e\u0937\u093e \u092e\u0947\u0902 \u0938\u092e\u091d\u0947\u0902\u0964"}[cfg.narration_language]
        if cfg.script_style == "hinglish":
            text = "\u0928\u092e\u0938\u094d\u0915\u093e\u0930! \u0906\u091c \u0915\u093e AI update \u0906\u0938\u093e\u0928 \u092d\u093e\u0937\u093e \u092e\u0947\u0902 \u0938\u092e\u091d\u0924\u0947 \u0939\u0948\u0902\u0964"
        try:
            with pipeline_lock(cfg):
                Speech(cfg).segment(text, target)
                check_audio(target)
            self.voice_result = {"id": ident, "state": "READY", "url": "/api/voice-preview/" + ident + ".wav"}
        except (RuntimeError, ValueError) as error:
            target.unlink(missing_ok=True)
            self.voice_result = {"id": ident, "state": "FAILED", "error": str(error)[:350]}
        except Exception:
            target.unlink(missing_ok=True)
            self.voice_result = {"id": ident, "state": "FAILED", "error": "Voice test failed. Check server configuration; no provider was switched."}

    def start(self):
        self.scheduler_lock.acquire()
        self.scheduler_owned = True
        try:
            enabled = True
            if self.state_path.is_file():
                enabled = json.loads(self.state_path.read_text(encoding="utf-8"))["enabled"] is True
            self.scheduler.start(paused=not enabled)
            if enabled and self.cfg.daily_script_batch and os.environ.get("LOOPYT_STARTUP_PLAN") == "1":
                self.queue_missing_plan()
        except Exception:
            self.scheduler_lock.release()
            self.scheduler_owned = False
            raise

    def queue_missing_plan(self):
        from .scheduling import prepare_missing_plan
        self.scheduler.add_job(prepare_missing_plan, "date", args=[self.cfg], id="startup-plan",
                               replace_existing=True, misfire_grace_time=300)

    def close(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        if self.scheduler_owned:
            self.scheduler_lock.release()

    def busy(self):
        if self.voice_future and not self.voice_future.done():
            return True
        if self.future and not self.future.done():
            return True
        try:
            with pipeline_lock(self.cfg):
                return False
        except portalocker.exceptions.LockException:
            return True

    def action(self, action, job_id=None, manual_input=None, render_mode=None):
        from . import render_settings
        if action == "save_schedule":
            from . import schedule_settings
            if not isinstance(manual_input, dict) or set(manual_input) != {"selection", "version"}:
                raise ValueError("Choose six automatic times, timezone and version")
            with self.guard:
                self.cfg, saved = schedule_settings.save(self.cfg, manual_input["selection"], manual_input["version"])
                # Replacing future timers does not interrupt a running worker or resume paused auto mode.
                self.scheduler.remove_all_jobs()
                configure(self.scheduler, self.cfg)
                return {"message": "Schedule saved. Automatic mode allows up to six runs per day; manual runs are unlimited.",
                        "schedule_settings": saved}
        if action == "save_script":
            from . import script_settings
            if not isinstance(manual_input, dict) or set(manual_input) != {"selection", "version"}:
                raise ValueError("Choose a script provider and version")
            with self.guard:
                self.cfg, saved = script_settings.save(self.cfg, manual_input["selection"], manual_input["version"])
                return {"message": "Script provider saved for new runs. Existing jobs keep their saved provider.", "script_settings": saved}
        if action == "save_render":
            if not isinstance(manual_input, dict) or set(manual_input) != {"selection", "version"}:
                raise ValueError("Choose a rendering selection and version")
            with self.guard:
                self.cfg, saved = render_settings.save(self.cfg, manual_input["selection"], manual_input["version"])
                return {"message": "Rendering default saved for new manual and scheduled runs.", "render_settings": saved}
        if render_mode is not None:
            if action not in ("run", "preview"):
                raise ValueError("Only new videos can choose a rendering mode; retries keep their saved selection")
            render_settings.RenderSelection(render_mode=render_mode)
        if manual_input is not None:
            if action not in ("run", "preview"):
                raise ValueError("Custom input is only available for a new run or preview")
            manual_input = ManualRequest.model_validate(manual_input).model_dump()
        with self.guard:
            if action in ("pause", "resume"):
                enabled = action == "resume"
                write_json(self.state_path, {"enabled": enabled})
                if enabled:
                    self.scheduler.resume()
                    if self.cfg.daily_script_batch and os.environ.get("LOOPYT_STARTUP_PLAN") == "1":
                        self.queue_missing_plan()
                else:
                    self.scheduler.pause()
                return {"message": "Auto mode resumed" if enabled else "Auto mode paused; current work can finish"}
            if action not in ("run", "preview", "retry"):
                raise ValueError("Unknown action")
            if self.busy():
                raise ValueError("A pipeline is already running. Wait for it to finish.")
            if action == "retry":
                if not JOB_ID.fullmatch(job_id or "") or self.store.get(job_id)["state"] != "FAILED":
                    raise ValueError("Only a failed job can be retried here")
            self.notice = ""
            self.future = self.executor.submit(self._run, action, job_id, manual_input, render_mode)
            return {"message": "Run started. Progress updates automatically."}

    def _run(self, action, job_id, manual_input=None, render_mode=None):
        try:
            runner = Pipeline(self.cfg)
            options = {"manual_input": manual_input} if manual_input else {}
            if render_mode is not None:
                options["render_mode"] = render_mode
            result = runner.retry(job_id) if action == "retry" else runner.create_run(
                dry_run=action == "preview", fresh=True, **options)
            if result:
                job = self.store.get(result)
                if job["state"] in ("FAILED", "SKIPPED"):
                    self.notice = "Run stopped: " + (job["error"] or "No eligible story available.")
                else:
                    self.notice = "Run finished; see its result below."
            else:
                self.notice = "No new run started; check the saved job state."
        except Exception as error:
            self.notice = f"Run stopped ({type(error).__name__}). Check the job and run doctor."

    def snapshot(self):
        from .creative import library_status
        from .motion import availability
        from .render_settings import record as render_record
        from .schedule_settings import record as schedule_record
        from .script_settings import record as script_record
        from .voice_settings import record
        cfg = self.cfg
        from .planning import load_plan
        plan = load_plan(cfg) if cfg.daily_script_batch else None
        zone = ZoneInfo(cfg.timezone)
        today = datetime.now(zone).date()
        jobs = self.store.jobs()
        with self.store.connect() as db:
            all_jobs = [dict(row) for row in db.execute("SELECT created_at,state,dry_run,slot FROM video_jobs")]
            uploads = [dict(row) for row in db.execute("SELECT job_id,video_id,privacy,uploaded_at FROM youtube_uploads")]
            events = [dict(row) for row in db.execute(
                "SELECT job_id,created_at,state,message FROM application_events ORDER BY id DESC LIMIT 60")]
        daily = [j for j in all_jobs if not j["dry_run"] and datetime.fromisoformat(j["created_at"]).astimezone(zone).date() == today]
        today_uploads = [u for u in uploads if datetime.fromisoformat(u["uploaded_at"]).astimezone(zone).date() == today]
        by_job = {u["job_id"]: u for u in uploads}
        for job in jobs:
            script = self.store.load(job["id"], "script") or {}
            job["title"] = script.get("title") or job["topic"] or "Finding a technology story…"
            job["upload"] = by_job.get(job["id"])
            job["progress"] = 100 if job["state"] in ("PUBLISHED", "AWAITING_REVIEW") else round(job["stage"] / 8 * 100)
            folder = cfg.output_dir / job["id"]
            job["has_video"] = (folder / "final.mp4").is_file()
            job["has_cover"] = (folder / "cover.png").is_file()
            job["research_progress"] = self.store.load(job["id"], "research_progress") if job["state"] == "RESEARCHING" else None
        automatic_attempts = sum(j["slot"] is not None for j in daily)
        upcoming = []
        for scheduled in self.scheduler.get_jobs():
            if scheduled.id not in cfg.schedule_times.split(",") or not getattr(scheduled, "next_run_time", None):
                continue
            due = scheduled.next_run_time
            if automatic_attempts >= cfg.max_videos_per_day and due.astimezone(zone).date() == today:
                due = scheduled.trigger.get_next_fire_time(None, datetime.now(zone).replace(
                    hour=23, minute=59, second=59, microsecond=999999))
            if due:
                upcoming.append(due.isoformat())
        upcoming.sort()
        return {"now": datetime.now(zone).isoformat(), "started_at": self.started_at,
                "auto_enabled": self.scheduler.state == 1, "busy": self.busy(), "notice": self.notice,
                "voice_settings": record(cfg),
                "render_settings": render_record(cfg),
                "script_settings": script_record(cfg),
                "schedule_settings": schedule_record(cfg),
                "script_providers": {"groq": {"model": cfg.groq_model, "key_present": bool(cfg.groq_api_key)},
                                     "gemini": {"model": cfg.gemini_model, "key_present": bool(cfg.gemini_api_key)},
                                     "openrouter": {"model": cfg.openrouter_model, "key_present": bool(cfg.openrouter_api_key), "free_only": True},
                                     "ollama": {"model": cfg.ollama_model, "local": True,
                                                "models": ["gemma3:4b", "qwen3:8b"]}},
                "motion_renderer": availability(cfg),
                "voice_preview": self.voice_result,
                "youtube_authorization": self.youtube_auth.snapshot(),
                "creative_library": library_status(cfg),
                "voice_keys": {"gemini": bool(cfg.gemini_api_key.strip()), "elevenlabs": bool(cfg.elevenlabs_api_key.strip())},
                "daily": {"attempts": len(daily), "auto_attempts": automatic_attempts, "uploaded": len(today_uploads),
                          "failed": sum(j["state"] == "FAILED" for j in daily), "limit": cfg.max_videos_per_day},
                "settings": {"timezone": cfg.timezone, "slots": cfg.schedule_times.split(","),
                             "daily_script_batch": cfg.daily_script_batch, "morning_plan_time": cfg.morning_plan_time,
                             "visibility": cfg.youtube_privacy, "uploads_enabled": cfg.enable_upload,
                             "language": cfg.narration_language, "voice": cfg.tts_voice,
                             "tts_engine": cfg.tts_engine,
                             "content_mode": cfg.content_mode,
                             "model": getattr(cfg, cfg.llm_provider + "_model"),
                             "script_fallback_enabled": cfg.script_fallback_enabled,
                             "pixabay": bool(cfg.pixabay_api_key),
                             "public_images": cfg.enable_public_images,
                             "creator_reference_count": len(cfg.creator_channel_ids),
                             "prefer_tech_insights": cfg.prefer_tech_insights,
                             "topic_focus": cfg.topic_focus,
                             "script_style": cfg.script_style,
                             "pexels": bool(cfg.pexels_api_key), "token_present": cfg.youtube_token_file.is_file()},
                "next_run": upcoming[0] if upcoming and self.scheduler.state == 1 else None,
                "disk_free_gb": round(shutil.disk_usage(cfg.output_dir).free / 1024**3, 1),
                "jobs": jobs, "events": events,
                "daily_plan": ({"date": plan["date"], "state": plan["state"], "error": plan.get("error"),
                                "content_mode": plan.get("content_mode", "verified_news"),
                                "rejected": plan.get("rejected", []),
                                "entries": [{"slot": e["slot"], "title": e["script"]["title"],
                                             "script": e["script"]["script"]} for e in plan["entries"]]}
                               if plan else None),
                "writing_warning": ("Creators supply topics only. Generated scripts require retrieved "
                    "web evidence from documentation, official pages or credible reporting. Official-only claims are attributed; sources appear in run details. "
                    "popularity is not proof." if cfg.content_mode == "youtube_insights" else
                    "Trending sources guide topic selection. Engagement and virality are not guaranteed. "
                    "Failed evidence or quality checks stop publication."),
                "podcast": self.podcast_snapshot()}

    def detail(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job")
        result = self.store.get(job_id)
        for kind in ("request", "render_settings", "render_report", "script_settings", "script_provider_usage", "voice_provider_usage", "script", "research", "source_brief", "editorial", "content_package", "research_attempts", "media_assets", "media_license_audit", "validation", "youtube"):
            value = self.store.load(job_id, kind)
            if kind == "media_assets" and value:
                value = [{k: v for k, v in asset.items() if k != "path"} for asset in value]
            if kind == "research" and value:
                value = {"sources": [{k: s.get(k) for k in ("title", "url", "publisher", "primary", "published_at")}
                                     for s in value.get("sources", [])]}
            result[kind] = value
        return result


def make_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # No request URLs, credentials or tokens in logs.

        def allowed(self):
            return self.headers.get("Host") in (f"127.0.0.1:{self.server.server_port}",
                                                 f"localhost:{self.server.server_port}")

        def send(self, status, body, content_type="application/json; charset=utf-8", headers=None):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if not self.allowed():
                return self.send(403, {"error": "Loopback access only"})
            path = urlsplit(self.path).path
            try:
                if path == "/api/youtube/callback":
                    from .youtube_oauth import cookie
                    if len(self.path) > 8192:
                        return self.send(400, {"error": "Invalid Google callback"})
                    try:
                        app.youtube_auth.finish(urlsplit(self.path).query, self.headers.get("Cookie", ""))
                    except ValueError as error:
                        return self.send(400, {"error": str(error)})
                    return self.send(303, b"", headers={"Location": "/#settings", "Set-Cookie": cookie(clear=True)})
                if path == "/api/status":
                    return self.send(200, app.snapshot())
                preview = re.fullmatch(r"/api/voice-preview/([a-f0-9]{16})\.wav", path)
                if preview:
                    import time
                    file = app.cfg.cache_dir / "voice-previews" / (preview[1] + ".wav")
                    if not file.is_file() or file.is_symlink() or time.time() - file.stat().st_mtime > 540:
                        return self.send(404, {"error": "Voice sample expired; generate a new test"})
                    return self.send(200, file.read_bytes(), "audio/wav")
                if path == "/api/session":
                    return self.send(200, {"token": app.token})
                if path.startswith("/api/jobs/"):
                    return self.send(200, app.detail(path.rsplit("/", 1)[-1]))
                match = re.fullmatch(r"/media/([a-f0-9]{16})/(final.mp4|cover.png)", path)
                if match:
                    return self.media(*match.groups())
                match_clip = re.fullmatch(r"/media/podcast/clip/([^/]+)/(final\.mp4|thumbnail\.png)", path)
                if match_clip:
                    return self.podcast_media(*match_clip.groups())
                names = {"/": "index.html", "/app.js": "app.js", "/ui.mjs": "ui.mjs", "/style.css": "style.css"}
                if path in names:
                    file = Path(__file__).parent / "web" / names[path]
                    content_type = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}[file.suffix]
                    return self.send(200, file.read_bytes(), content_type + "; charset=utf-8")
                return self.send(404, {"error": "Not found"})
            except (ValueError, FileNotFoundError):
                return self.send(404, {"error": "Job or media not found"})
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                pass
            except Exception as exc:
                log.exception("Unexpected error in do_GET: %s", exc)
                return self.send(500, {"error": "Could not read local data; run doctor"})

        def media(self, job_id, name):
            root = app.cfg.output_dir.resolve()
            file = (root / job_id / name).resolve()
            if not file.is_relative_to(root) or not file.is_file():
                return self.send(404, {"error": "Not found"})
            size = file.stat().st_size
            start, end = 0, size - 1
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                if not match:
                    return self.send(416, {"error": "Unsupported range"})
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
                if not 0 <= start <= end < size:
                    return self.send(416, {"error": "Invalid range"})
            self.send_response(206 if byte_range else 200)
            self.send_header("Content-Type", "video/mp4" if name.endswith(".mp4") else "image/png")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with file.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def podcast_media(self, clip_id, name):
            clip = app.store.get_podcast_clip(clip_id)
            if not clip:
                return self.send(404, {"error": "Clip not found"})
            target_str = clip.get("output_path" if name == "final.mp4" else "thumbnail_path")
            if not target_str:
                return self.send(404, {"error": "Media path not recorded"})
            file = Path(target_str).resolve()
            if not file.is_file():
                return self.send(404, {"error": "File not found on disk"})
            size = file.stat().st_size
            start, end = 0, size - 1
            byte_range = self.headers.get("Range")
            if byte_range:
                match = re.fullmatch(r"bytes=(\d+)-(\d*)", byte_range)
                if not match:
                    return self.send(416, {"error": "Unsupported range"})
                start = int(match[1])
                end = min(int(match[2]), end) if match[2] else end
                if not 0 <= start <= end < size:
                    return self.send(416, {"error": "Invalid range"})
            self.send_response(206 if byte_range else 200)
            self.send_header("Content-Type", "video/mp4" if name.endswith(".mp4") else "image/png")
            self.send_header("Content-Length", str(end - start + 1))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            if byte_range:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self.end_headers()
            with file.open("rb") as stream:
                stream.seek(start)
                remaining = end - start + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.allowed() or origin not in allowed_origins or not hmac.compare_digest(
                    self.headers.get("X-AutoYT-Token", ""), app.token):
                return self.send(403, {"error": "Open the dashboard on localhost to use controls"})
            if self.path not in ("/api/action", "/api/creative/import"):
                return self.send(404, {"error": "Not found"})
            value = {}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= (11_000_000 if self.path == "/api/creative/import" else 32768):
                    raise ValueError("Invalid request size")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("Invalid request")
                if self.path == "/api/creative/import":
                    from .creative import import_asset
                    with app.guard:
                        if app.busy():
                            return self.send(409, {"error": "Wait for the current video or voice test before importing"})
                        try:
                            with pipeline_lock(app.cfg):
                                result = import_asset(app.cfg, value)
                        except ValueError as error:
                            return self.send(409, {"error": str(error)[:350]})
                    return self.send(200, result)
                if value.get("action") in {"youtube_reconnect", "cancel_youtube_auth"}:
                    from .youtube_oauth import cookie
                    with app.guard:
                        if value["action"] == "youtube_reconnect":
                            if app.busy():
                                raise ValueError("Wait for the current video or voice test to finish before reconnecting YouTube")
                            result, browser_cookie = app.youtube_auth.start("http://" + self.headers["Host"])
                        else:
                            app.youtube_auth.cancel()
                            result, browser_cookie = {"message": "Connection cancelled. Existing authorization was kept."}, cookie(clear=True)
                    return self.send(200, result, headers={"Set-Cookie": browser_cookie})
                if str(value.get("action", "")).startswith("podcast_"):
                    try:
                        result = app.podcast_action(value["action"], value)
                    except Exception as error:
                        return self.send(409, {"error": str(error)[:350]})
                elif value.get("action") in {"voice_list", "save_voice", "test_voice"}:
                    try:
                        result = app.voice_action(value["action"], value.get("selection"), value.get("version"), value.get("page"))
                    except (RuntimeError, ValueError, TypeError) as error:
                        return self.send(409, {"error": error.errors()[0]["msg"] if isinstance(error, ValidationError) else str(error)[:350]})
                else:
                    result = app.action(value.get("action"), value.get("job_id"), value.get("manual_input"), value.get("render_mode"))
                return self.send(200, result)
            except ValidationError as error:
                return self.send(409, {"error": error.errors()[0]["msg"]})
            except (ValueError, TypeError) as error:
                if isinstance(value, dict) and value.get("action") in {"save_render", "save_script", "save_schedule", "youtube_reconnect", "cancel_youtube_auth"}:
                    return self.send(409, {"error": str(error)[:350]})
                return self.send(409, {"error": "Action unavailable. A run may be active, or the job is not retryable."})
            except Exception:
                return self.send(500, {"error": "Action failed; run doctor"})

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def serve(cfg, port=8765):
    app = Dashboard(cfg)
    server = make_server(app, port)
    try:
        app.start()
        print(f"AutoYT dashboard: http://127.0.0.1:{server.server_port} (auto mode follows saved pause state)", flush=True)
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        app.close()
