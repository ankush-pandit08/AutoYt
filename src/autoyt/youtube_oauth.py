"""Dashboard-initiated Google consent, bound to one browser and pinned channel."""
import hashlib
import hmac
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit

from . import youtube
from .pipeline import pipeline_lock

CALLBACK = "/api/youtube/callback"
COOKIE = "autoyt_youtube_auth"
TTL = 600


def cookie(value="", clear=False):
    return f"{COOKIE}={value}; Path={CALLBACK}; Max-Age={0 if clear else TTL}; HttpOnly; SameSite=Lax"


class YouTubeOAuth:
    def __init__(self, cfg):
        self.cfg = cfg
        self.lock = threading.Lock()
        self.pending = None
        self.result = None

    def _expire(self):
        if self.pending and time.monotonic() >= self.pending["expires"]:
            self.pending = None
            self.result = {"state": "expired", "message": "Connection request expired. Click Reconnect YouTube to try again."}

    def snapshot(self):
        with self.lock:
            self._expire()
            result = dict(youtube.authorization_status(self.cfg))
            if self.result and self.result["state"] == "connected" and result["state"] == "reconnect_required":
                self.result = None
            result.update(self.result or {})
            if self.pending:
                result.update(state="waiting", message="Finish signing in with Google in the other tab. Select your configured YouTube channel.")
            return result

    def start(self, origin):
        from google_auth_oauthlib.flow import Flow

        parts = urlsplit(origin)
        if parts.scheme != "http" or parts.hostname not in {"localhost", "127.0.0.1"} or parts.username or parts.password or parts.path or parts.query or parts.fragment:
            raise ValueError("Open AutoYT on localhost to reconnect YouTube")
        if not self.cfg.youtube_channel_id:
            raise ValueError("Complete initial channel setup with autoyt auth and pin YOUTUBE_CHANNEL_ID first")
        if not self.cfg.youtube_client_secrets_file.is_file():
            raise ValueError("Google OAuth client JSON is missing. Add it to the configured secrets folder first")
        with self.lock:
            if self.result and self.result["state"] == "verifying":
                raise ValueError("Google connection is being verified. Please wait")
            try:
                config = json.loads(self.cfg.youtube_client_secrets_file.read_text(encoding="utf-8"))
                client = config.get("installed") or config.get("web") or {}
                if client.get("auth_uri") not in {"https://accounts.google.com/o/oauth2/auth", "https://accounts.google.com/o/oauth2/v2/auth"} or client.get("token_uri") not in {"https://oauth2.googleapis.com/token", "https://accounts.google.com/o/oauth2/token"}:
                    raise ValueError("Not a Google OAuth configuration")
                state, binding = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                flow = Flow.from_client_config(config, scopes=youtube.SCOPES, state=state,
                                              autogenerate_code_verifier=True)
                flow.redirect_uri = origin + CALLBACK
                url, _ = flow.authorization_url(access_type="offline", prompt="consent")
            except Exception:
                raise ValueError("Google OAuth client JSON could not be loaded; check the configured client file") from None
            if urlsplit(url).hostname != "accounts.google.com" or urlsplit(url).scheme != "https":
                raise ValueError("Google authorization endpoint is invalid")
            self.pending = {"state": state, "binding": hashlib.sha256(binding.encode()).digest(),
                            "flow": flow, "expires": time.monotonic() + TTL}
            self.result = None
            return {"authorization_url": url, "message": "Sign in with Google and select the configured channel. No video will be uploaded."}, cookie(binding)

    def cancel(self):
        with self.lock:
            if self.result and self.result["state"] == "verifying":
                raise ValueError("Connection verification is already in progress")
            self.pending = None
            self.result = {"state": "cancelled", "message": "Connection cancelled. Existing authorization was kept."}

    def finish(self, query, browser_cookie):
        values = parse_qs(query, keep_blank_values=True)
        try:
            jar = SimpleCookie()
            jar.load(browser_cookie or "")
            binding = jar[COOKIE].value
        except Exception:
            binding = ""
        with self.lock:
            self._expire()
            attempt = self.pending
            if (not attempt or len(values.get("state", [])) != 1 or
                    not hmac.compare_digest(values["state"][0], attempt["state"]) or
                    not hmac.compare_digest(hashlib.sha256(binding.encode()).digest(), attempt["binding"])):
                raise ValueError("This connection request expired or belongs to another browser. Start again from AutoYT")
            self.pending = None  # Consume before network I/O; callbacks cannot be replayed.
            self.result = {"state": "verifying", "message": "Verifying your YouTube channel…"}
        if values.get("error") or len(values.get("code", [])) != 1 or not values["code"][0]:
            self.result = {"state": "cancelled", "message": "Google sign-in was cancelled or incomplete. Existing authorization was kept."}
            return
        try:
            with pipeline_lock(self.cfg):
                attempt["flow"].fetch_token(code=values["code"][0], timeout=30)
                credentials = attempt["flow"].credentials
                if not credentials.refresh_token or not credentials.valid:
                    raise ValueError("Offline authorization was not granted")
                scopes = set(credentials.granted_scopes or credentials.scopes or [])
                if not set(youtube.SCOPES) <= scopes:
                    raise ValueError("Required YouTube access was not granted")
                youtube.authorized_channel(self.cfg, credentials, timeout=20)
                youtube.save_credentials(self.cfg, credentials)
            self.result = {"state": "connected", "message": "YouTube reconnected. You can now retry the saved failed video. No upload was started."}
        except Exception:
            self.result = {"state": "failed", "message": "Could not reconnect. Select the configured YouTube channel, grant both requested permissions, and check Google client setup. Existing authorization was kept; retry when no video is running."}
