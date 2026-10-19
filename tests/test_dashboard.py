import json
import threading
from urllib.request import Request, urlopen

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from autoyt.config import Settings
from autoyt.dashboard import Dashboard, make_server
from autoyt.licensing import audit
from autoyt.scheduling import configure


def test_ten_slots_and_transactional_limit(cfg):
    settings = Settings(_env_file=None, schedule_times="06:00,07:40,09:20,11:00,12:40,14:20,16:00,17:40,19:20,21:00",
                        max_videos_per_day=10)
    assert len(settings.schedule_times.split(",")) == settings.max_videos_per_day == 10
    scheduler = configure(BackgroundScheduler(), settings)
    assert len(scheduler.get_jobs()) == 10
    app = Dashboard(cfg)
    assert all(app.store.create(slot=f"2026-10-18T{i:02}:00", limit=10) for i in range(10))
    assert app.store.create(slot="2026-10-18T11:00", limit=10) is None
    assert app.store.create(limit=10)  # Run button jobs have no slot or daily app cap.
    app.close()


def test_pause_survives_restart_and_hides_secrets(cfg):
    cfg.pixabay_api_key = "PRIVATE_PIXABAY_KEY"
    cfg.pexels_api_key = "PRIVATE_PEXELS_KEY"
    app = Dashboard(cfg)
    app.start()
    try:
        app.action("pause")
        snapshot = app.snapshot()
        assert not snapshot["auto_enabled"]
        assert snapshot["next_run"] is None
        encoded = json.dumps(snapshot)
        assert "PRIVATE_PIXABAY_KEY" not in encoded and "PRIVATE_PEXELS_KEY" not in encoded
    finally:
        app.close()
    restarted = Dashboard(cfg)
    restarted.start()
    try:
        assert not restarted.snapshot()["auto_enabled"]
        restarted.action("resume")
        assert restarted.snapshot()["auto_enabled"]
        assert restarted.snapshot()["next_run"]
    finally:
        restarted.close()


def test_daily_counts_include_jobs_outside_recent_hundred(cfg):
    app = Dashboard(cfg)
    for _ in range(105):
        job_id = app.store.create(dry_run=True)
        app.store.update(job_id, "FAILED")
    job_id = app.store.create(limit=10)
    app.store.confirm_upload(job_id, "example-video", "public")
    app.start()
    try:
        snapshot = app.snapshot()
        assert len(snapshot["jobs"]) == 100
        assert snapshot["daily"]["attempts"] == snapshot["daily"]["uploaded"] == 1
    finally:
        app.close()


def test_http_access_controls_and_media_ranges(cfg):
    app = Dashboard(cfg)
    app.start()
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    job_id = app.store.create()
    folder = cfg.output_dir / job_id
    folder.mkdir()
    (folder / "final.mp4").write_bytes(b"0123456789")
    try:
        with urlopen(base) as response:
            assert b"Channel overview" in response.read()
            assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
        for headers in ({"Host": "evil.example"},):
            with pytest.raises(Exception) as caught:
                urlopen(Request(base + "/api/status", headers=headers))
            assert caught.value.code == 403
        body = json.dumps({"action": "pause"}).encode()
        for headers in ({}, {"Origin": "https://evil.example", "X-AutoYT-Token": app.token},
                        {"Origin": base, "X-AutoYT-Token": "wrong"}):
            with pytest.raises(Exception) as caught:
                urlopen(Request(base + "/api/action", data=body, headers=headers))
            assert caught.value.code == 403
        with urlopen(Request(base + "/api/action", data=body, headers={
                "Origin": base, "X-AutoYT-Token": app.token})) as response:
            assert response.status == 200
        assert not app.snapshot()["auto_enabled"]
        with urlopen(Request(base + f"/media/{job_id}/final.mp4", headers={"Range": "bytes=2-5"})) as response:
            assert response.status == 206
            assert response.read() == b"2345"
        with pytest.raises(Exception) as caught:
            urlopen(base + "/media/../../.env")
        assert caught.value.code == 404
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


@pytest.mark.parametrize("asset", [
    {"kind": "stock_image", "source_url": "https://random.example/image.jpg", "license": "free", "attribution": "Unknown"},
    {"kind": "stock", "source_url": "https://pixabay.com.evil.example/video", "license": "Pixabay Content License https://pixabay.com/service/license-summary/", "attribution": "Someone"},
    {"kind": "graphics", "license": "Copyrighted image"},
])
def test_reject_undocumented_or_impostor_media(asset):
    with pytest.raises(ValueError):
        audit([asset])


def test_free_licensed_media_audit():
    result = audit([
        {"kind": "graphics", "license": "Original AutoYT graphics"},
        {"kind": "stock_image", "source_url": "https://pixabay.com/photos/example-123/",
         "license": "Pixabay Content License https://pixabay.com/service/license-summary/", "attribution": "Creator on Pixabay"},
        {"kind": "stock", "source_url": "https://www.pexels.com/video/123/",
         "license": "Pexels License https://www.pexels.com/license/", "attribution": "Creator on Pexels"},
    ])
    assert result["passed"] and len(result["assets"]) == 3


def test_run_notice_explains_failure(cfg, monkeypatch):
    from types import SimpleNamespace

    app = Dashboard(cfg)
    job = app.store.create()
    app.store.update(job, "FAILED", error="No verified story available; official source missing")
    monkeypatch.setattr("autoyt.dashboard.Pipeline", lambda cfg: SimpleNamespace(
        create_run=lambda **kwargs: job))
    try:
        app._run("run", None)
        assert "official source missing" in app.notice
        assert "Run finished" not in app.notice
    finally:
        app.close()
