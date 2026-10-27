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
                    "Failed evidence or quality checks stop publication.")}

    def detail(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job")
        result = self.store.get(job_id)
        for kind in ("request", "render_settings", "render_report", "script_settings", "script", "research", "source_brief", "editorial", "content_package", "research_attempts", "media_assets", "media_license_audit", "validation", "youtube"):
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

        def send(self, status, body, content_type="application/json; charset=utf-8"):
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
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
                names = {"/": "index.html", "/app.js": "app.js", "/ui.mjs": "ui.mjs", "/style.css": "style.css"}
                if path in names:
                    file = Path(__file__).parent / "web" / names[path]
                    content_type = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}[file.suffix]
                    return self.send(200, file.read_bytes(), content_type + "; charset=utf-8")
                return self.send(404, {"error": "Not found"})
            except (ValueError, FileNotFoundError):
                return self.send(404, {"error": "Job or media not found"})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
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

        def do_POST(self):
            origin = self.headers.get("Origin")
            allowed_origins = {f"http://127.0.0.1:{self.server.server_port}", f"http://localhost:{self.server.server_port}"}
            if not self.allowed() or origin not in allowed_origins or not hmac.compare_digest(
                    self.headers.get("X-AutoYT-Token", ""), app.token):
                return self.send(403, {"error": "Open the dashboard on localhost to use controls"})
            if self.path != "/api/action":
                return self.send(404, {"error": "Not found"})
            value = {}
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid request size")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("Invalid request")
                if value.get("action") in {"voice_list", "save_voice", "test_voice"}:
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
                if isinstance(value, dict) and value.get("action") in {"save_render", "save_script", "save_schedule"}:
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
