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
    from google.oauth2.credentials import Credentials
    cfg.youtube_token_file.parent.mkdir()
    cfg.youtube_token_file.write_text(json.dumps({"refresh_token": "test"}))
    def fail(*args):
        raise RuntimeError("secret refresh failure")
    credentials = SimpleNamespace(expired=True, refresh_token="test", refresh=fail)
    monkeypatch.setattr(Credentials, "from_authorized_user_file", lambda *args: credentials)
    with pytest.raises(RuntimeError, match="revoked"):
        youtube.service(cfg)
