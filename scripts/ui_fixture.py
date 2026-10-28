"""Local-only dashboard QA server. Fake data, no providers, no uploads, no saved preferences."""
import argparse
import json
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from autoyt.schedule_settings import DEFAULT_TIMES, ScheduleSelection

WEB = Path(__file__).resolve().parents[1] / "src/autoyt/web"
stamp = datetime.now(timezone.utc).isoformat()
data = {
    "now": stamp, "started_at": "ui-fixture", "auto_enabled": True, "busy": False, "notice": "",
    "daily": {"attempts": 3, "auto_attempts": 2, "uploaded": 1, "failed": 1, "limit": 6},
    "schedule_settings": {"version": 0, "selection": {"times": DEFAULT_TIMES, "timezone": "Asia/Kolkata"}},
    "script_settings": {"version": 0, "selection": {"provider": "groq"}},
    "render_settings": {"version": 0, "selection": {"render_mode": "hybrid", "render_fallback": True,
                                                  "motion_icons": True, "motion_lottie": True, "sound_effects": True}},
    "creative_library": {"animations": 0, "sounds": 0, "items": []},
    "voice_settings": {"version": 0, "selection": {"provider": "kokoro", "voice": "am_michael", "language": "en"},
                       "voices": {"kokoro": "am_michael", "gemini": "Puck"}},
    "script_providers": {"groq": {"model": "fixture-groq-model", "key_present": True},
                         "gemini": {"model": "fixture-gemini-model", "key_present": True}},
    "voice_keys": {"gemini": False, "elevenlabs": False}, "voice_preview": None,
    "youtube_authorization": {"state": "reconnect_required", "client_present": True, "channel_pinned": True},
    "motion_renderer": {"ready": True}, "next_run": stamp, "disk_free_gb": 100,
    "settings": {"timezone": "Asia/Kolkata", "slots": DEFAULT_TIMES, "daily_script_batch": False,
                 "morning_plan_time": "05:00", "uploads_enabled": True, "visibility": "public",
                 "language": "en", "script_style": "standard", "voice": "am_michael", "tts_engine": "kokoro",
                 "content_mode": "youtube_insights", "model": "fixture-groq-model", "topic_focus": "ai_coding",
                 "script_fallback_enabled": True,
                 "public_images": True, "pixabay": False, "pexels": False, "token_present": True},
    "jobs": [{"id": "a"*16, "title": "A local preview", "state": "READY_TO_UPLOAD", "dry_run": 1, "stage": 7,
              "progress": 88, "created_at": stamp, "has_cover": False, "has_video": False},
             {"id": "b"*16, "title": "A failed example", "state": "FAILED", "dry_run": 0, "stage": 3,
              "progress": 38, "created_at": stamp, "has_cover": False, "has_video": False}],
    "events": [], "daily_plan": None, "writing_warning": "UI fixture only. No real video can be created here.",
}
controls = {"offline": False, "fail_save": False, "delay": .4}
lock = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, status, body, content_type="application/json"):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        if self.path == '/api/jobs/' + 'a'*16:
            return self.send(200, {**data['jobs'][0],
                'script_settings': {'provider':'groq','model':'fixture-groq-model'},
                'script_provider_usage': {'preferred_provider':'groq','active_provider':'gemini','calls':[
                    {'provider':'groq','model':'fixture-groq-model','schema':'NaturalEditorialDraft',
                     'status':'unavailable','reason':'rate_limited'},
                    {'provider':'gemini','model':'fixture-gemini-model','schema':'NaturalEditorialDraft','status':'success'}]}})
        if self.path == "/api/status":
            return self.send(503, {"error": "Fixture offline"}) if controls["offline"] else self.send(200, data)
        if self.path == "/api/session":
            return self.send(200, {"token": "ui-fixture-only"})
        names = {"/": "index.html", "/app.js": "app.js", "/ui.mjs": "ui.mjs", "/style.css": "style.css"}
        if self.path in names:
            path = WEB/names[self.path]
            mime = {".html": "text/html", ".js": "text/javascript", ".mjs": "text/javascript", ".css": "text/css"}[path.suffix]
            return self.send(200, path.read_bytes(), mime)
        return self.send(404, {"error": "Fixture resource not found"})

    def do_POST(self):
        value = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))))
        if self.path == "/__fixture":
            controls.update({key: value[key] for key in controls if key in value})
            return self.send(200, controls)
        action = value.get("action", "")
        if action not in {"save_script", "save_render", "save_voice", "save_schedule"}:
            return self.send(409, {"error": "No generation, voice API or upload is available in this UI fixture."})
        time.sleep(controls["delay"])
        if controls["fail_save"]:
            return self.send(409, {"error": "Test save rejected. Your previous settings are still active."})
        kind = action.removeprefix("save_")
        request = value if kind == "voice" else value["manual_input"]
        if kind == "schedule":
            try:
                request["selection"] = ScheduleSelection.model_validate(request["selection"]).model_dump()
            except ValueError:
                return self.send(409, {"error": "Choose six different automatic run times and a valid timezone"})
        key = kind+"_settings"
        with lock:
            if request["version"] != data[key]["version"]:
                return self.send(409, {"error": "Settings changed elsewhere. Reload the saved selection."})
            data[key] = {"version": request["version"]+1, "selection": request["selection"]}
            if kind == "schedule":
                data["settings"]["slots"] = request["selection"]["times"]
                data["settings"]["timezone"] = request["selection"]["timezone"]
            return self.send(200, {"message": "Fixture settings saved for this test page only.", key: data[key]})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print("UI fixture only: http://127.0.0.1:"+str(args.port), flush=True)
    server.serve_forever()
