from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import portalocker
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .pipeline import Pipeline


def configure(scheduler, cfg):
    if Path(".server-managed").is_file():
        raise RuntimeError("LoopYt runs on the OmniKit server. This Windows scheduler is disabled.")
    if cfg.daily_script_batch:
        hour, minute = map(int, cfg.morning_plan_time.split(":"))
        scheduler.add_job(morning_plan, CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone),
                          args=[cfg], id="morning-plan", max_instances=1, coalesce=True, misfire_grace_time=55)
    for slot in cfg.schedule_times.split(","):
        hour, minute = map(int, slot.split(":"))
        scheduler.add_job(scheduled_run, CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone),
                          args=[cfg, slot], id=slot, max_instances=1, coalesce=True, misfire_grace_time=55)
    return scheduler


def morning_plan(cfg):
    from .planning import prepare_day
    try:
        return prepare_day(cfg)
    except portalocker.exceptions.LockException:
        return None


def prepare_missing_plan(cfg, moment=None):
    from .planning import load_plan, prepare_day
    local = (moment or datetime.now(ZoneInfo(cfg.timezone))).astimezone(ZoneInfo(cfg.timezone))
    if not cfg.daily_script_batch or local.strftime("%H:%M") < cfg.morning_plan_time or load_plan(cfg, local):
        return None
    remaining = [s for s in cfg.schedule_times.split(",") if s > local.strftime("%H:%M")]
    if not remaining:
        return None
    try:
        return prepare_day(cfg.model_copy(update={"schedule_times": ",".join(remaining)}), local)
    except portalocker.exceptions.LockException:
        return None


def slot_key(cfg, moment):
    return moment.astimezone(ZoneInfo(cfg.timezone)).strftime("%Y-%m-%dT%H:%M")


def scheduled_run(cfg, expected=None, moment=None):
    moment = moment or datetime.now(ZoneInfo(cfg.timezone))
    local = moment.astimezone(ZoneInfo(cfg.timezone))
    slot = expected or local.strftime("%H:%M")
    hour, minute = map(int, slot.split(":"))
    due = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    # Skip old missed runs, even if called manually with an obsolete expected slot.
    if not timedelta(0) <= local-due < timedelta(seconds=60):
        return None
    try:
        if cfg.daily_script_batch:
            from .planning import load_plan
            plan = load_plan(cfg, moment)
            entry = next((e for e in (plan or {}).get("entries", []) if e["slot"] == slot), None)
            if entry is None:
                return None  # Morning-only discovery: never replace a missing script at upload time.
            return Pipeline(cfg).create_run(slot=slot_key(cfg, due), prepared=entry)
        return Pipeline(cfg).create_run(slot=slot_key(cfg, due))
    except portalocker.exceptions.LockException:
        return None


def serve(cfg):
    cfg.prepare()
    with portalocker.Lock(str(cfg.database_path)+".scheduler.lock", timeout=0):
        scheduler = configure(BlockingScheduler(timezone=ZoneInfo(cfg.timezone)), cfg)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown(wait=True)
