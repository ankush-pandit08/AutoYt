import json
import threading
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest

from autoyt import youtube
from autoyt.dashboard import Dashboard, make_server
from autoyt.youtube_oauth import CALLBACK, COOKIE, YouTubeOAuth


@pytest.fixture
def auth_cfg(cfg):
    cfg.youtube_channel_id = "UC-pinned-channel"
    cfg.youtube_client_secrets_file.parent.mkdir(parents=True, exist_ok=True)
    cfg.youtube_client_secrets_file.write_text(json.dumps({"installed": {
        "client_id": "test-client.apps.googleusercontent.com", "client_secret": "private-client-secret",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "token_uri": "https://oauth2.googleapis.com/token", "redirect_uris": ["http://localhost"]}}))
    cfg.youtube_token_file.write_text("old-token-kept-until-success")
    return cfg


def start(manager):
    result, cookie = manager.start("http://127.0.0.1:8765")
    values = parse_qs(urlsplit(result["authorization_url"]).query)
    return values, cookie.split(";", 1)[0]


def install_flow(manager, monkeypatch, *, wrong_channel=False):
    calls = []
    creds = SimpleNamespace(refresh_token="private-new-refresh", valid=True, granted_scopes=youtube.SCOPES,
        scopes=youtube.SCOPES, to_json=lambda: '{"refresh_token":"private-new-refresh"}')
    manager.pending["flow"] = SimpleNamespace(credentials=creds,
        fetch_token=lambda **kwargs: calls.append(kwargs))
    def check(cfg, credentials, **kwargs):
        assert cfg.youtube_token_file.read_text() == "old-token-kept-until-success"
        if wrong_channel:
            raise RuntimeError("OAuth channel does not match configured channel")
        return None, {"id": cfg.youtube_channel_id}
    monkeypatch.setattr(youtube, "authorized_channel", check)
    return calls


def test_pkce_offline_consent_and_browser_cookie(auth_cfg):
    auth = YouTubeOAuth(auth_cfg)
    values, cookie = start(auth)
    assert values["code_challenge_method"] == ["S256"] and values["code_challenge"]
    assert values["access_type"] == ["offline"] and values["prompt"] == ["consent"]
    assert values["redirect_uri"] == ["http://127.0.0.1:8765" + CALLBACK]
    assert set(values["scope"][0].split()) == set(youtube.SCOPES)
    assert cookie.startswith(COOKIE + "=")
    _, header = auth.start("http://localhost:8765")
    assert "HttpOnly" in header and "SameSite=Lax" in header and "Max-Age=600" in header
    encoded = json.dumps(auth.snapshot())
    for secret in ("private-client-secret", values["state"][0], cookie.split("=", 1)[1]):
        assert secret not in encoded


def test_callback_state_cookie_single_use_and_atomic_save(auth_cfg, monkeypatch):
    auth = YouTubeOAuth(auth_cfg)
    values, cookie = start(auth)
    calls = install_flow(auth, monkeypatch)
    query = urlencode({"state": values["state"][0], "code": "private-code"})
    for invalid, header in [("state=wrong&code=private-code", cookie), (query, ""), (query, COOKIE + "=wrong")]:
        with pytest.raises(ValueError, match="another browser"):
            auth.finish(invalid, header)
    assert not calls and auth.pending
    auth.finish(query, cookie)
    assert len(calls) == 1
    assert json.loads(auth_cfg.youtube_token_file.read_text())["refresh_token"] == "private-new-refresh"
    assert auth.snapshot()["state"] == "connected"
    assert "private-new-refresh" not in json.dumps(auth.snapshot())
    with pytest.raises(ValueError):
        auth.finish(query, cookie)
    assert len(calls) == 1
    youtube.mark_authorization(auth_cfg, "reconnect_required")
    assert auth.snapshot()["state"] == "reconnect_required"


def test_wrong_channel_keeps_old_token(auth_cfg, monkeypatch):
    auth = YouTubeOAuth(auth_cfg)
    values, cookie = start(auth)
    install_flow(auth, monkeypatch, wrong_channel=True)
    auth.finish(urlencode({"state": values["state"][0], "code": "private-code"}), cookie)
    assert auth.snapshot()["state"] == "failed"
    assert auth_cfg.youtube_token_file.read_text() == "old-token-kept-until-success"


@pytest.mark.parametrize("failure", ["no_refresh", "missing_scope", "network"])
def test_incomplete_or_failed_google_exchange_preserves_token(auth_cfg, monkeypatch, failure):
    auth = YouTubeOAuth(auth_cfg)
    values, cookie = start(auth)
    install_flow(auth, monkeypatch)
    flow = auth.pending["flow"]
    if failure == "no_refresh":
        flow.credentials.refresh_token = None
    elif failure == "missing_scope":
        flow.credentials.granted_scopes = youtube.SCOPES[:1]
    else:
        flow.fetch_token = lambda **kw: (_ for _ in ()).throw(RuntimeError("private-exchange-error"))
    auth.finish(urlencode({"state": values["state"][0], "code": "private-code"}), cookie)
    assert auth.snapshot()["state"] == "failed"
    assert auth_cfg.youtube_token_file.read_text() == "old-token-kept-until-success"
    assert "private-exchange-error" not in json.dumps(auth.snapshot())


def test_google_cancellation_expiry_and_cancel_button_keep_token(auth_cfg):
    auth = YouTubeOAuth(auth_cfg)
    values, cookie = start(auth)
    auth.finish(urlencode({"state": values["state"][0], "error": "access_denied"}), cookie)
    assert auth.snapshot()["state"] == "cancelled"
    values, cookie = start(auth)
    auth.pending["expires"] = 0
    with pytest.raises(ValueError):
        auth.finish(urlencode({"state": values["state"][0], "code": "code"}), cookie)
    assert auth.snapshot()["state"] == "expired"
    start(auth)
    auth.cancel()
    assert auth.pending is None and auth.snapshot()["state"] == "cancelled"
    assert auth_cfg.youtube_token_file.read_text() == "old-token-kept-until-success"


def test_reconnect_requires_configured_channel_and_safe_google_config(auth_cfg):
    auth = YouTubeOAuth(auth_cfg)
    with pytest.raises(ValueError, match="localhost"):
        auth.start("https://evil.example")
    auth_cfg.youtube_channel_id = ""
    with pytest.raises(ValueError, match="initial channel setup"):
        start(auth)
    auth_cfg.youtube_channel_id = "UC-pinned-channel"
    auth_cfg.youtube_client_secrets_file.write_text('{"installed":{"auth_uri":"https://evil.example"}}')
    with pytest.raises(ValueError, match="could not be loaded"):
        start(auth)


def test_reconnect_http_csrf_cookie_and_clean_callback_redirect(auth_cfg, monkeypatch):
    app = Dashboard(auth_cfg)
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_port}"
        with httpx.Client(base_url=base) as client:
            assert client.post("/api/action", json={"action": "youtube_reconnect"}).status_code == 403
            headers = {"Origin": base, "X-AutoYT-Token": app.token}
            result = client.post("/api/action", headers=headers, json={"action": "youtube_reconnect"})
            assert result.status_code == 200 and "HttpOnly" in result.headers["set-cookie"]
            values = parse_qs(urlsplit(result.json()["authorization_url"]).query)
            calls = install_flow(app.youtube_auth, monkeypatch)
            result = client.get(CALLBACK, params={"state": values["state"][0], "code": "private-code"})
            assert result.status_code == 303 and result.headers["location"] == "/#settings"
            assert "private-code" not in result.text
            assert len(calls) == 1 and not app.future
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)
        app.close()
