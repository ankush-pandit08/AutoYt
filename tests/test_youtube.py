import json
from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

from autoyt import youtube
from autoyt.store import Store, digest, write_json


class Execute:
    def __init__(self, value):
        self.value = value

    def execute(self):
        return self.value


class UploadRequest:
    def __init__(self, failures=None):
        self.resumable_uri = None
        self.resumable_progress = 0
        self.failures = failures or []
        self._in_error_state = False
        self.calls = 0

    def next_chunk(self, num_retries):
        self.calls += 1
        self.resumable_uri = "https://upload.example/session-private-token"
        if self.failures:
            raise self.failures.pop(0)
        return None, {"id": "video-123"}


class FakeYouTube:
    def __init__(self, request, privacy="private", processing="succeeded"):
        self.request, self.privacy, self.processing = request, privacy, processing
        self.inserts = []

    def videos(self):
        return self

    def insert(self, **kwargs):
        self.inserts.append(kwargs)
        return self.request

    def list(self, **kwargs):
        return Execute({"items": [{"id": "video-123", "status": {"privacyStatus": self.privacy,
                           "uploadStatus": "processed"}, "processingDetails": {"processingStatus": self.processing}}]})


@pytest.fixture
def ready(cfg, script):
    cfg.enable_upload = True
    cfg.youtube_channel_id = "channel-123"
    cfg.prepare()
    store = Store(cfg.database_path)
    job_id = store.create()
    folder = cfg.output_dir/job_id
    folder.mkdir()
    (folder/"final.mp4").write_bytes(b"test network boundary fixture")
    write_json(folder/"validation.json", {"passed": True})
    store.update(job_id, "READY_TO_UPLOAD", stage=7, content_hash=digest(folder/"final.mp4"))
    store.save(job_id, "media_assets", [{"kind": "graphics", "license": "Original AutoYT graphics",
                                       "attribution": "Original graphics"}])
    store.save(job_id, "metadata", youtube.metadata(script, job_id, [{"attribution": "Original graphics"}]))
    return cfg, store, job_id, folder


def install_fake(monkeypatch, fake):
    monkeypatch.setattr(youtube, "service", lambda cfg: (fake, {"id": "channel-123"}))
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)


def test_private_upload_and_idempotency(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    fake = FakeYouTube(UploadRequest())
    install_fake(monkeypatch, fake)
    assert youtube.upload(cfg, store, job_id, folder) == "video-123"
    assert fake.inserts[0]["body"]["status"]["privacyStatus"] == "private"
    assert "publishAt" not in fake.inserts[0]["body"]["status"]
    assert store.get(job_id)["state"] == "AWAITING_REVIEW"
    youtube.upload(cfg, store, job_id, folder)
    assert len(fake.inserts) == 1


def test_unapproved_media_blocks_upload_before_network(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    store.save(job_id, "media_assets", [{"kind": "stock_image", "license": "free",
                                       "source_url": "https://unknown.example/photo", "attribution": "unknown"}])
    def forbidden(*args):
        pytest.fail("Unapproved media must stop before YouTube access")
    monkeypatch.setattr(youtube, "service", forbidden)
    with pytest.raises(ValueError, match="license"):
        youtube.upload(cfg, store, job_id, folder)


def test_cc_by_credits_required_at_upload(ready, script, monkeypatch):
    cfg, store, job_id, folder = ready
    asset = {"kind": "stock", "license": "CC-BY-4.0", "free_to_use": True,
             "source_url": "https://creator.example/asset", "permitted_use": "Edited videos",
             "license_url": "https://creativecommons.org/licenses/by/4.0/", "attribution": "Creator name"}
    store.save(job_id, "media_assets", [asset])
    with pytest.raises(ValueError, match="credits"):
        youtube.upload(cfg, store, job_id, folder)
    body = youtube.metadata(script, job_id, [asset], include_description=True)
    assert all(value in body["snippet"]["description"] for value in (asset["attribution"], asset["license_url"], asset["source_url"]))
    assert "Edited/cropped" in body["snippet"]["description"]


def test_old_job_retry_cannot_exceed_daily_upload_cap(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    for index in range(cfg.max_videos_per_day):
        previous = store.create(dry_run=True, slot=f"2026-10-18T{index:02}:00")
        store.confirm_upload(previous, f"previous-{index}", "public")
    with store.connect() as db:
        db.execute("UPDATE video_jobs SET slot=? WHERE id=?", ("2026-10-18T23:00", job_id))
    def forbidden(*args):
        pytest.fail("Daily cap must stop a new scheduled upload before network access")
    monkeypatch.setattr(youtube, "service", forbidden)
    with pytest.raises(RuntimeError, match="daily scheduled upload limit"):
        youtube.upload(cfg, store, job_id, folder)


def test_authorized_public_upload_and_retry_keep_visibility(ready, script, monkeypatch):
    cfg, store, job_id, folder = ready
    cfg.youtube_privacy = "public"
    store.save(job_id, "metadata", youtube.metadata(script, job_id, [{"attribution": "Original graphics"}],
                                                   privacy=cfg.youtube_privacy))
    fake = FakeYouTube(UploadRequest(), privacy="public")
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    assert fake.inserts[0]["body"]["status"]["privacyStatus"] == "public"
    assert store.get(job_id)["state"] == "PUBLISHED"
    assert store.load(job_id, "youtube")["privacy"] == "public"
    with store.connect() as db:
        assert db.execute("SELECT privacy FROM youtube_uploads WHERE job_id=?", (job_id,)).fetchone()[0] == "public"
    cfg.youtube_privacy = "private"
    youtube.upload(cfg, store, job_id, folder)
    assert len(fake.inserts) == 1


def test_public_default_does_not_republish_existing_private_job(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    cfg.youtube_privacy = "public"
    fake = FakeYouTube(UploadRequest())
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    assert fake.inserts[0]["body"]["status"]["privacyStatus"] == "private"
    assert store.get(job_id)["state"] == "AWAITING_REVIEW"


def test_public_requested_but_private_returned_is_not_reported_as_published(ready, script, monkeypatch):
    cfg, store, job_id, folder = ready
    store.save(job_id, "metadata", youtube.metadata(script, job_id, [{"attribution": "Original graphics"}],
                                                   privacy="public"))
    install_fake(monkeypatch, FakeYouTube(UploadRequest(), privacy="private"))
    with pytest.raises(RuntimeError, match="requested public"):
        youtube.upload(cfg, store, job_id, folder)
    assert store.attempt(job_id)["video_id"] == "video-123"
    assert store.get(job_id)["state"] != "PUBLISHED"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_retryable_server(ready, monkeypatch, status):
    cfg, store, job_id, folder = ready
    error = HttpError(httplib2.Response({"status": str(status)}), b'{"error":{"message":"retry"}}')
    request = UploadRequest([error])
    fake = FakeYouTube(request)
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    assert request.calls == 2 and len(fake.inserts) == 1


@pytest.mark.parametrize("status", [401, 403, 404])
def test_auth_quota_or_expired_session_no_blind_retry(ready, monkeypatch, status):
    cfg, store, job_id, folder = ready
    error = HttpError(httplib2.Response({"status": str(status)}), b'{"error":{"message":"denied"}}')
    request = UploadRequest([error])
    fake = FakeYouTube(request)
    install_fake(monkeypatch, fake)
    with pytest.raises(RuntimeError, match="session saved"):
        youtube.upload(cfg, store, job_id, folder)
    assert request.calls == 1
    assert store.attempt(job_id)["session_uri"] == request.resumable_uri


def test_interrupted_upload_resume(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    store.upload_progress(job_id, "INTERRUPTED", "https://upload.example/existing", 4096)
    request = UploadRequest()
    fake = FakeYouTube(request)
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    assert request._in_error_state
    assert request.resumable_progress == 4096


def test_ambiguous_initiation_halts(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    store.upload_progress(job_id, "INITIATING")
    fake = FakeYouTube(UploadRequest())
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(youtube, "reconcile", lambda *args: None)
    with pytest.raises(RuntimeError, match="ambiguous"):
        youtube.upload(cfg, store, job_id, folder)
    assert not fake.inserts


def test_ambiguous_success_reconciles(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    store.upload_progress(job_id, "INITIATING")
    fake = FakeYouTube(UploadRequest())
    install_fake(monkeypatch, fake)
    monkeypatch.setattr(youtube, "reconcile", lambda *args: "video-123")
    assert youtube.upload(cfg, store, job_id, folder) == "video-123"
    assert not fake.inserts


@pytest.mark.parametrize("privacy,processing", [("public", "succeeded"), ("private", "failed"),
                                               ("private", "processing")])
def test_visibility_and_processing_gate(ready, monkeypatch, privacy, processing):
    cfg, store, job_id, folder = ready
    fake = FakeYouTube(UploadRequest(), privacy, processing)
    install_fake(monkeypatch, fake)
    with pytest.raises(RuntimeError):
        youtube.upload(cfg, store, job_id, folder)
    assert store.attempt(job_id)["video_id"] == "video-123"
    with store.connect() as db:
        assert db.execute("SELECT count(*) FROM youtube_uploads").fetchone()[0] == 0


def test_dry_run_upload_gate(ready):
    cfg, store, job_id, folder = ready
    with store.connect() as db:
        db.execute("UPDATE video_jobs SET dry_run=1 WHERE id=?", (job_id,))
    with pytest.raises(ValueError, match="Dry-run"):
        youtube.upload(cfg, store, job_id, folder)


def test_tampered_file_gate(ready):
    cfg, store, job_id, folder = ready
    (folder/"final.mp4").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        youtube.upload(cfg, store, job_id, folder)


def test_disabled_gate(ready):
    cfg, store, job_id, folder = ready
    cfg.enable_upload = False
    with pytest.raises(RuntimeError, match="ENABLE_UPLOAD"):
        youtube.upload(cfg, store, job_id, folder)


def test_missing_oauth(cfg):
    with pytest.raises(RuntimeError, match="consent"):
        youtube.service(cfg)


def test_revoked_refresh(cfg, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    cfg.youtube_token_file.parent.mkdir()
    cfg.youtube_token_file.write_text(json.dumps({"refresh_token": "test"}))
    def fail(*args):
        raise RefreshError("invalid_grant", {"error": "invalid_grant"})
    credentials = SimpleNamespace(expired=True, refresh_token="test", refresh=fail)
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    with pytest.raises(RuntimeError, match="revoked"):
        youtube.service(cfg)


@pytest.mark.parametrize("kind", ["transport", "server"])
def test_temporary_refresh_failure_is_not_reported_as_revoked(cfg, monkeypatch, kind):
    from google.auth.exceptions import RefreshError, TransportError
    from google.oauth2.credentials import Credentials

    cfg.youtube_token_file.parent.mkdir()
    cfg.youtube_token_file.write_text("{}")
    calls = []
    def fail(*args):
        calls.append(1)
        if kind == "transport":
            raise TransportError("PRIVATE_TRANSPORT_DETAILS")
        raise RefreshError("PRIVATE_TRANSPORT_DETAILS", retryable=True)
    credentials = SimpleNamespace(expired=True, refresh_token="test", refresh=fail)
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)
    with pytest.raises(RuntimeError, match="TRANSIENT:") as caught:
        youtube.service(cfg)
    assert len(calls) == 3
    assert "revoked" not in str(caught.value)
    assert "PRIVATE_TRANSPORT_DETAILS" not in str(caught.value)


def test_transient_refresh_recovers_and_saves_token(cfg, monkeypatch):
    from google.auth.exceptions import TransportError
    from google.oauth2.credentials import Credentials

    cfg.youtube_channel_id = "channel-123"
    cfg.youtube_token_file.parent.mkdir()
    cfg.youtube_token_file.write_text("{}")
    calls = []
    def refresh(*args):
        calls.append(1)
        if len(calls) == 1:
            raise TransportError("Temporary network outage")
    credentials = SimpleNamespace(expired=True, valid=True, refresh_token="test", refresh=refresh,
                                  to_json=lambda: '{"refreshed":true}')
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    monkeypatch.setattr(youtube.time, "sleep", lambda _: None)
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *args, **kwargs:
        SimpleNamespace(channels=lambda: SimpleNamespace(list=lambda **kwargs:
            Execute({"items": [{"id": "channel-123"}]}))))
    assert youtube.service(cfg)[1]["id"] == "channel-123"
    assert len(calls) == 2
    assert json.loads(cfg.youtube_token_file.read_text())["refreshed"]


def test_auth_can_reopen_consent_after_invalid_grant(cfg, monkeypatch):
    from google.auth.exceptions import RefreshError
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    cfg.youtube_channel_id = "channel-123"
    cfg.youtube_token_file.parent.mkdir()
    cfg.youtube_token_file.write_text("{}")
    cfg.youtube_client_secrets_file.write_text("{}")
    def revoked(*args):
        raise RefreshError("invalid_grant", {"error": "invalid_grant"})
    old = SimpleNamespace(expired=True, refresh_token="test", refresh=revoked)
    new = SimpleNamespace(valid=True, to_json=lambda: '{"fresh_consent":true}')
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: old)
    def flow(path, scopes):
        assert scopes == youtube.SCOPES
        return SimpleNamespace(run_local_server=lambda port: new)
    monkeypatch.setattr(InstalledAppFlow, "from_client_secrets_file", flow)
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *args, **kwargs:
        SimpleNamespace(channels=lambda: SimpleNamespace(list=lambda **kwargs:
            Execute({"items": [{"id": "channel-123"}]}))))
    assert youtube.service(cfg, interactive=True)[1]["id"] == "channel-123"
    assert json.loads(cfg.youtube_token_file.read_text())["fresh_consent"]


def test_service_transport_preserves_resumable_308_responses(cfg, monkeypatch):
    from google.oauth2.credentials import Credentials

    cfg.youtube_channel_id = "channel-123"
    cfg.youtube_token_file.parent.mkdir(parents=True)
    cfg.youtube_token_file.write_text("{}")
    credentials = SimpleNamespace(expired=False, valid=True, to_json=lambda: "{}")
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    def build(*args, **kwargs):
        transport = kwargs["http"].http
        assert 308 not in transport.redirect_codes
        assert transport.timeout == 300
        return SimpleNamespace(channels=lambda: SimpleNamespace(list=lambda **kwargs:
            Execute({"items": [{"id": "channel-123"}]})))
    monkeypatch.setattr("googleapiclient.discovery.build", build)
    assert youtube.service(cfg)[1]["id"] == "channel-123"


def test_rejected_visual_upload_stays_blocked_until_reconciled(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    store.save(job_id, "upload_block", {"blocked": True})
    monkeypatch.setattr(youtube, "service", lambda *args: pytest.fail("Blocked job must not reach YouTube"))
    with pytest.raises(RuntimeError, match="reconciliation"):
        youtube.upload(cfg, store, job_id, folder)


def test_reconcile_excludes_cancelled_upload_entries():
    items = [{"snippet": {"description": "AutoYT job: job-123"}, "contentDetails": {"videoId": video}}
             for video in ("cancelled-video", "replacement-video")]
    api = SimpleNamespace(playlistItems=lambda: SimpleNamespace(list=lambda **kwargs: Execute({"items": items})))
    channel = {"contentDetails": {"relatedPlaylists": {"uploads": "uploads-playlist"}}}
    assert youtube.reconcile(api, channel, "job-123", ["cancelled-video"]) == "replacement-video"


def test_empty_description_preserves_recovery_tag(script):
    body = youtube.metadata(script, "job-123", [{"attribution": "Original graphics"}], include_description=False)
    assert body["snippet"]["description"] == ""
    assert "AutoYT-job-job-123" in body["snippet"]["tags"]


def test_default_metadata_has_short_summary_hashtags_and_topic_keywords(script):
    script.title = "OpenAI releases a new coding model"
    script.topic = script.title
    body = youtube.metadata(script, "job-123", [{"attribution": "Original graphics"}])
    snippet = body["snippet"]
    assert snippet["description"].startswith(script.description)
    assert "#Shorts" in snippet["description"] and "#OpenAI" in snippet["description"]
    assert "AutoYT job:" not in snippet["description"]
    assert any("OpenAI" in tag for tag in snippet["tags"])
    assert "AutoYT-job-job-123" in snippet["tags"]
    assert all("#" not in tag for tag in snippet["tags"])


def test_unicode_and_large_metadata_fit_youtube_limits(script):
    script.description = "नई तकनीक और AI की जानकारी। Technology update. " * 50
    script.topic = " ".join("technology" + str(i) for i in range(120))
    script.hashtags = ["#AI", "#ai", "#Technology", "#Shorts"]
    snippet = youtube.metadata(script, "job-123", [])["snippet"]
    assert len(snippet["description"].split("\n\n")[0]) <= 320
    assert len(snippet["description"].encode("utf-8")) <= 4900
    assert len({t.casefold() for t in snippet["tags"]}) == len(snippet["tags"])
    tags = snippet["tags"]
    assert sum(len(t) + (2 if " " in t else 0) for t in tags) + len(tags)-1 <= 500
    assert "robotics" not in [t.casefold() for t in tags]


def test_pending_job_gets_description_without_changing_frozen_visibility(ready, script, monkeypatch):
    cfg, store, job_id, folder = ready
    cfg.youtube_privacy = "public"
    store.save(job_id, "script", script.model_dump(mode="json"))
    body = store.load(job_id, "metadata")
    body["snippet"]["description"] = ""
    store.save(job_id, "metadata", body)
    fake = FakeYouTube(UploadRequest(), privacy="private")
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    sent = fake.inserts[0]["body"]
    assert sent["snippet"]["description"] and "#Shorts" in sent["snippet"]["description"]
    assert sent["status"]["privacyStatus"] == "private"
    assert json.loads((folder / "metadata.json").read_text())["snippet"] == sent["snippet"]


def test_resumed_upload_keeps_original_metadata(ready, script, monkeypatch):
    cfg, store, job_id, folder = ready
    store.save(job_id, "script", script.model_dump(mode="json"))
    body = store.load(job_id, "metadata")
    body["snippet"]["description"] = ""
    body["snippet"]["tags"] = [f"AutoYT-job-{job_id}"]
    store.save(job_id, "metadata", body)
    store.upload_progress(job_id, "SESSION", "https://upload.example/saved", 12)
    class Resume(UploadRequest):
        def next_chunk(self, num_retries):
            self.uri_at_resume = self.resumable_uri
            return super().next_chunk(num_retries)
    fake = FakeYouTube(Resume())
    install_fake(monkeypatch, fake)
    youtube.upload(cfg, store, job_id, folder)
    assert fake.inserts[0]["body"]["snippet"] == body["snippet"]
    assert fake.request.uri_at_resume == "https://upload.example/saved"


def test_reconcile_empty_description_by_tag():
    api = SimpleNamespace(
        playlistItems=lambda: SimpleNamespace(list=lambda **kwargs: Execute({"items": [
            {"snippet": {"description": ""}, "contentDetails": {"videoId": "replacement"}}]})),
        videos=lambda: SimpleNamespace(list=lambda **kwargs: Execute({"items": [
            {"id": "replacement", "snippet": {"tags": ["AutoYT-job-job-123"]}}]})))
    channel = {"contentDetails": {"relatedPlaylists": {"uploads": "uploads-playlist"}}}
    assert youtube.reconcile(api, channel, "job-123") == "replacement"


def test_manual_upload_allowed_after_scheduled_cap(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    for index in range(cfg.max_videos_per_day):
        previous = store.create(dry_run=True, slot=f"2026-10-18T{index:02}:00")
        store.confirm_upload(previous, f"scheduled-{index}", "public")
    assert store.daily_upload_count(cfg.timezone, scheduled_only=True) == cfg.max_videos_per_day
    install_fake(monkeypatch, FakeYouTube(UploadRequest()))
    assert youtube.upload(cfg, store, job_id, folder) == "video-123"
    assert store.daily_upload_count(cfg.timezone, scheduled_only=True) == cfg.max_videos_per_day


def test_manual_uploads_do_not_consume_scheduled_allowance(ready, monkeypatch):
    cfg, store, job_id, folder = ready
    for index in range(cfg.max_videos_per_day):
        previous = store.create(dry_run=True)
        store.confirm_upload(previous, f"manual-{index}", "public")
    with store.connect() as db:
        db.execute("UPDATE video_jobs SET slot=? WHERE id=?", ("2026-10-18T23:00", job_id))
    assert store.daily_upload_count(cfg.timezone, scheduled_only=True) == 0
    install_fake(monkeypatch, FakeYouTube(UploadRequest()))
    assert youtube.upload(cfg, store, job_id, folder) == "video-123"
