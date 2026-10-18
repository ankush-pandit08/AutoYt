"""One loopback HTTP server, one scheduler, the existing SQLite pipeline. No cloud services."""
import hmac
import json
import logging
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

from .pipeline import Pipeline, pipeline_lock
from .scheduling import configure
from .store import Store, write_json

log = logging.getLogger(__name__)
JOB_ID = re.compile(r"^[a-f0-9]{16}$")
TERMINAL = {"PUBLISHED", "AWAITING_REVIEW", "FAILED", "SKIPPED", "READY_TO_UPLOAD"}


class Dashboard:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg.prepare()
        self.store = Store(cfg.database_path)
        self.state_path = cfg.database_path.parent / "dashboard-state.json"
        self.token = secrets.token_urlsafe(32)
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="autoyt-manual")
        self.guard = threading.Lock()
        self.future = None
        self.notice = ""
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.scheduler = configure(BackgroundScheduler(timezone=cfg.timezone), cfg)
        self.scheduler_lock = portalocker.Lock(str(cfg.database_path) + ".scheduler.lock", timeout=0)
        self.scheduler_owned = False

    def start(self):
        self.scheduler_lock.acquire()
        self.scheduler_owned = True
        try:
            enabled = True
            if self.state_path.is_file():
                enabled = json.loads(self.state_path.read_text(encoding="utf-8"))["enabled"] is True
            self.scheduler.start(paused=not enabled)
        except Exception:
            self.scheduler_lock.release()
            self.scheduler_owned = False
            raise

    def close(self):
        if self.scheduler.running:
            self.scheduler.shutdown(wait=True)
        self.executor.shutdown(wait=True)
        if self.scheduler_owned:
            self.scheduler_lock.release()

    def busy(self):
        if self.future and not self.future.done():
            return True
        try:
            with pipeline_lock(self.cfg):
                return False
        except portalocker.exceptions.LockException:
            return True

    def action(self, action, job_id=None):
        with self.guard:
            if action in ("pause", "resume"):
                enabled = action == "resume"
                write_json(self.state_path, {"enabled": enabled})
                if enabled:
                    self.scheduler.resume()
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
            self.future = self.executor.submit(self._run, action, job_id)
            return {"message": "Run started. Progress updates automatically."}

    def _run(self, action, job_id):
        try:
            runner = Pipeline(self.cfg)
            result = runner.retry(job_id) if action == "retry" else runner.create_run(dry_run=action == "preview")
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
        cfg = self.cfg
        from .planning import load_plan
        plan = load_plan(cfg) if cfg.daily_script_batch else None
        zone = ZoneInfo(cfg.timezone)
        today = datetime.now(zone).date()
        jobs = self.store.jobs()
        with self.store.connect() as db:
            all_jobs = [dict(row) for row in db.execute("SELECT created_at,state,dry_run FROM video_jobs")]
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
        upcoming = sorted(j.next_run_time.isoformat() for j in self.scheduler.get_jobs()
                          if getattr(j, "next_run_time", None))
        return {"now": datetime.now(zone).isoformat(), "started_at": self.started_at,
                "auto_enabled": self.scheduler.state == 1, "busy": self.busy(), "notice": self.notice,
                "daily": {"attempts": len(daily), "uploaded": len(today_uploads),
                          "failed": sum(j["state"] == "FAILED" for j in daily), "limit": cfg.max_videos_per_day},
                "settings": {"timezone": cfg.timezone, "slots": cfg.schedule_times.split(","),
                             "daily_script_batch": cfg.daily_script_batch, "morning_plan_time": cfg.morning_plan_time,
                             "visibility": cfg.youtube_privacy, "uploads_enabled": cfg.enable_upload,
                             "language": cfg.narration_language, "voice": cfg.tts_voice,
                             "model": (cfg.gemini_model if cfg.llm_provider == "gemini" else
                                       cfg.groq_model if cfg.llm_provider == "groq" else cfg.ollama_model),
                             "pixabay": bool(cfg.pixabay_api_key),
                             "pexels": bool(cfg.pexels_api_key), "token_present": cfg.youtube_token_file.is_file()},
                "next_run": upcoming[0] if upcoming and self.scheduler.state == 1 else None,
                "disk_free_gb": round(shutil.disk_usage(cfg.output_dir).free / 1024**3, 1),
                "jobs": jobs, "events": events,
                "daily_plan": ({"date": plan["date"], "state": plan["state"], "error": plan.get("error"),
                                "rejected": plan.get("rejected", []),
                                "entries": [{"slot": e["slot"], "title": e["script"]["title"],
                                             "script": e["script"]["script"]} for e in plan["entries"]]}
                               if plan else None),
                "writing_warning": "Trending sources guide topic selection. Engagement and virality are not guaranteed. Failed evidence or quality checks stop publication."}

    def detail(self, job_id):
        if not JOB_ID.fullmatch(job_id):
            raise ValueError("Invalid job")
        result = self.store.get(job_id)
        for kind in ("script", "research", "research_attempts", "media_assets", "media_license_audit", "validation", "youtube"):
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
                if path == "/api/session":
                    return self.send(200, {"token": app.token})
                if path.startswith("/api/jobs/"):
                    return self.send(200, app.detail(path.rsplit("/", 1)[-1]))
                match = re.fullmatch(r"/media/([a-f0-9]{16})/(final.mp4|cover.png)", path)
                if match:
                    return self.media(*match.groups())
                names = {"/": "index.html", "/app.js": "app.js", "/style.css": "style.css"}
                if path in names:
                    file = Path(__file__).parent / "web" / names[path]
                    content_type = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}[file.suffix]
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
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("Invalid request size")
                value = json.loads(self.rfile.read(length))
                if not isinstance(value, dict):
                    raise ValueError("Invalid request")
                result = app.action(value.get("action"), value.get("job_id"))
                return self.send(200, result)
            except (ValueError, TypeError):
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
