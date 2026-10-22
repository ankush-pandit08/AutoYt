from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from autoyt import schedule_settings, scheduling
from autoyt.dashboard import Dashboard
from autoyt.store import Store


@pytest.mark.parametrize("selection", [
    {"times": ["09:30"] * 6, "timezone": "Asia/Kolkata"},
    {"times": ["09:30"] * 5, "timezone": "Asia/Kolkata"},
    {"times": ["24:00", "11:00", "12:00", "13:00", "14:00", "15:00"], "timezone": "UTC"},
    {"times": schedule_settings.DEFAULT_TIMES, "timezone": "Not/AZone"},
])
def test_schedule_rejects_invalid_input(selection):
    with pytest.raises(ValueError):
        schedule_settings.ScheduleSelection.model_validate(selection)


def test_legacy_schedule_migrates_to_six_and_survives_restart(cfg):
    cfg.schedule_times = "08:00,14:00,20:00"
    cfg.max_videos_per_day = 3
    cfg.prepare()
    updated = schedule_settings.initialize(cfg)
    assert updated.max_videos_per_day == 6
    assert updated.schedule_times.split(",") == schedule_settings.DEFAULT_TIMES
    assert schedule_settings.apply(cfg).schedule_times == updated.schedule_times


def test_saving_replaces_future_timers_and_preserves_pause(cfg):
    app = Dashboard(cfg)
    app.start()
    try:
        app.action("pause")
        selection = {"times": ["10:00", "12:00", "14:00", "16:00", "18:00", "20:00"], "timezone": "UTC"}
        result = app.action("save_schedule", manual_input={"selection": selection, "version": 0})
        assert result["schedule_settings"]["version"] == 1
        assert app.scheduler.state == 2
        assert {j.id for j in app.scheduler.get_jobs()} == set(selection["times"])
        assert app.snapshot()["next_run"] is None
        assert app.cfg.timezone == "UTC" and app.cfg.max_videos_per_day == 6
        with pytest.raises(ValueError, match="another session"):
            app.action("save_schedule", manual_input={"selection": selection, "version": 0})
    finally:
        app.close()
    restarted = Dashboard(cfg)
    try:
        assert restarted.cfg.schedule_times == ",".join(selection["times"])
        assert restarted.cfg.timezone == "UTC"
    finally:
        restarted.close()


def test_six_automatic_jobs_do_not_limit_manual_runs(cfg):
    store = Store(cfg.database_path)
    for i in range(10):
        assert store.create()
    for i in range(6):
        assert store.create(slot=f"2026-10-22T{i:02}:00", limit=6)
    assert store.create(slot="2026-10-22T20:00", limit=6) is None
    assert store.create()  # Manual runs stay available after the automatic limit.


def test_removed_timer_cannot_fire_after_schedule_changes(cfg, monkeypatch):
    cfg.prepare()
    schedule_settings.initialize(cfg)
    monkeypatch.setattr(scheduling, "Pipeline", lambda cfg: pytest.fail("Removed timer must not run"))
    moment = datetime(2026, 10, 22, 8, 0, tzinfo=ZoneInfo(cfg.timezone))
    assert scheduling.scheduled_run(cfg, "08:00", moment) is None


def test_next_run_excludes_preparation_and_respects_daily_cap(cfg):
    cfg.daily_script_batch = True
    app = Dashboard(cfg)
    app.start()
    try:
        snapshot = app.snapshot()
        due = datetime.fromisoformat(snapshot["next_run"])
        assert due.strftime("%H:%M") in app.cfg.schedule_times.split(",")
        for i in range(6):
            app.store.create(slot=f"today-{i}", limit=6)
        snapshot = app.snapshot()
        assert snapshot["daily"]["auto_attempts"] == 6
        assert datetime.fromisoformat(snapshot["next_run"]).date() > datetime.now(ZoneInfo(cfg.timezone)).date()
    finally:
        app.close()
