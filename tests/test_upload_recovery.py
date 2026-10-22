import json
from datetime import datetime
from zoneinfo import ZoneInfo
from types import SimpleNamespace

import httplib2
import pytest
from googleapiclient.errors import HttpError

from autoyt import youtube, scheduling
from autoyt.store import Store, digest, write_json
from test_youtube import FakeYouTube, UploadRequest, install_fake


@pytest.fixture
def ready(cfg, script):
    cfg.enable_upload=True;cfg.youtube_channel_id="test-channel";cfg.prepare()
    d=Store(cfg.database_path);job=d.create();folder=cfg.output_dir/job;folder.mkdir()
    (folder/"final.mp4").write_bytes(b"validated fixture")
    write_json(folder/"validation.json",{"passed":True})
    d.update(job,"READY_TO_UPLOAD",stage=7,content_hash=digest(folder/"final.mp4"))
    d.save(job,"script",script.model_dump(mode="json"))
    d.save(job,"media_assets",[{"kind":"graphics","license":"Original AutoYT graphics","attribution":"Original"}])
    d.save(job,"metadata",youtube.metadata(script,job,[{"attribution":"Original"}]))
    return cfg,d,job,folder


def test_session_header_is_durable_before_video_transfer(ready):
    cfg,d,job,folder=ready
    transport=SimpleNamespace(request=lambda *a,**kw:(httplib2.Response({"status":"200","location":"https://upload.example/secret"}),b""))
    wrapped=youtube.SessionTransport(transport,d,job)
    wrapped.request("https://upload.example/init","POST",body="metadata")
    assert d.attempt(job)["session_uri"]=="https://upload.example/secret"
    assert d.attempt(job)["state"]=="SESSION"


def test_explicit_initial_rejection_is_not_ambiguous_and_can_retry(ready,monkeypatch):
    cfg,d,job,folder=ready
    class Reject(UploadRequest):
        def next_chunk(self,num_retries):
            self.calls+=1
            raise HttpError(httplib2.Response({"status":"400"}),json.dumps({"error":{"errors":[{"reason":"uploadLimitExceeded","message":"secret-token"}]}}).encode())
    request=Reject();install_fake(monkeypatch,FakeYouTube(request))
    with pytest.raises(RuntimeError,match="channel upload limit") as error:youtube.upload(cfg,d,job,folder)
    assert request.calls==1 and "secret-token" not in str(error.value)
    assert d.attempt(job)["state"]=="REJECTED" and not d.attempt(job)["session_uri"]
    fake=FakeYouTube(UploadRequest());install_fake(monkeypatch,fake)
    assert youtube.upload(cfg,d,job,folder)=="video-123"
    assert len(fake.inserts)==1


def test_unknown_initial_timeout_never_sends_duplicate_insert(ready,monkeypatch):
    cfg,d,job,folder=ready
    class Timeout(UploadRequest):
        def next_chunk(self,num_retries):
            self.calls+=1;raise TimeoutError("private-url")
    request=Timeout();install_fake(monkeypatch,FakeYouTube(request))
    with pytest.raises(RuntimeError,match="ambiguous"):youtube.upload(cfg,d,job,folder)
    assert request.calls==1
    fake=FakeYouTube(UploadRequest());install_fake(monkeypatch,fake)
    monkeypatch.setattr(youtube,"reconcile",lambda *a:None)
    with pytest.raises(RuntimeError,match="ambiguous"):youtube.upload(cfg,d,job,folder)
    assert not fake.inserts


def test_processing_confirmation_survives_media_cleanup(ready,monkeypatch):
    cfg,d,job,folder=ready
    d.upload_progress(job,"RETURNED_ID",video_id="video-123")
    (folder/"final.mp4").unlink();(folder/"validation.json").unlink()
    fake=FakeYouTube(UploadRequest());install_fake(monkeypatch,fake)
    assert youtube.upload(cfg,d,job,folder)=="video-123"
    assert not fake.inserts


def test_rejected_job_with_expired_media_regenerates_from_saved_script(ready,monkeypatch):
    from autoyt.pipeline import Pipeline
    cfg,d,job,folder=ready;d.upload_progress(job,"REJECTED")
    d.update(job,"FAILED",stage=7);(folder/"final.mp4").unlink()
    monkeypatch.setattr(Pipeline,"execute",lambda self,ident:self.store.get(ident)["stage"])
    assert Pipeline(cfg).retry(job)==3
    assert d.load(job,"script")


@pytest.mark.parametrize("hour,expected",[(4,None),(12,"14:00,20:00"),(22,None)])
def test_startup_plan_uses_only_remaining_slots(cfg,monkeypatch,hour,expected):
    cfg.schedule_times="08:00,14:00,20:00"
    cfg.daily_script_batch=True
    monkeypatch.setattr("autoyt.planning.load_plan",lambda *a:None)
    monkeypatch.setattr("autoyt.planning.prepare_day",lambda c,m:c.schedule_times)
    moment=datetime(2026,10,20,hour,0,tzinfo=ZoneInfo(cfg.timezone))
    assert scheduling.prepare_missing_plan(cfg,moment)==expected


def test_startup_does_not_replace_existing_daily_plan(cfg,monkeypatch):
    cfg.daily_script_batch=True
    monkeypatch.setattr("autoyt.planning.load_plan",lambda *a:{"state":"PARTIAL"})
    monkeypatch.setattr("autoyt.planning.prepare_day",lambda *a:pytest.fail("Existing plan must stay unchanged"))
    assert scheduling.prepare_missing_plan(cfg,datetime(2026,10,20,12,tzinfo=ZoneInfo(cfg.timezone))) is None
