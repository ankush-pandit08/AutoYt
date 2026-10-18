from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import portalocker
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from .pipeline import Pipeline


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
        return Pipeline(cfg).create_run(slot=slot_key(cfg, due))
    except portalocker.exceptions.LockException:
        return None


def serve(cfg):
    cfg.prepare()
    with portalocker.Lock(str(cfg.database_path)+".scheduler.lock", timeout=0):
        scheduler = BlockingScheduler(timezone=ZoneInfo(cfg.timezone))
        for slot in cfg.schedule_times.split(","):
            hour, minute = map(int, slot.split(":"))
            scheduler.add_job(scheduled_run, CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone),
                              args=[cfg, slot], id=slot, max_instances=1, coalesce=True, misfire_grace_time=55)
        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            scheduler.shutdown(wait=True)
