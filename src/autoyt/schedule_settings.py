"""Persistent six-slot automatic schedule. Manual runs have no daily app limit."""
import json
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .store import write_json

DEFAULT_TIMES = ["09:30", "11:18", "13:06", "14:54", "16:42", "18:30"]


class ScheduleSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    times: list[str] = Field(min_length=6, max_length=6)
    timezone: str

    @field_validator("times")
    @classmethod
    def times_valid(cls, values):
        import re
        if any(not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value) for value in values):
            raise ValueError("Enter all six times in HH:MM format")
        if len(set(values)) != 6:
            raise ValueError("Choose six different automatic run times")
        return sorted(values)

    @field_validator("timezone")
    @classmethod
    def timezone_valid(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("Enter a valid timezone, such as Asia/Kolkata") from None
        return value


def record(cfg):
    path = cfg.database_path.parent / "schedule-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "version": 0, "selection": {"times": cfg.schedule_times.split(","), "timezone": cfg.timezone}}


def apply(cfg):
    path = cfg.database_path.parent / "schedule-settings.json"
    if not path.is_file():
        return cfg
    parsed = ScheduleSelection.model_validate(record(cfg)["selection"])
    candidate = cfg.model_copy(update={"schedule_times": ",".join(parsed.times),
                                      "timezone": parsed.timezone, "max_videos_per_day": 6})
    return type(cfg).model_validate(candidate.model_dump())


def initialize(cfg):
    path = cfg.database_path.parent / "schedule-settings.json"
    if not path.is_file():
        times = cfg.schedule_times.split(",")
        parsed = ScheduleSelection(times=times if len(times) == 6 else DEFAULT_TIMES, timezone=cfg.timezone)
        write_json(path, {"version": 0, "selection": parsed.model_dump()})
    return apply(cfg)


def save(cfg, selection, version):
    if type(version) is not int or version != record(cfg)["version"]:
        raise ValueError("Schedule changed in another session. Discard your draft and reload before saving")
    parsed = ScheduleSelection.model_validate(selection)
    candidate = cfg.model_copy(update={"schedule_times": ",".join(parsed.times),
                                      "timezone": parsed.timezone, "max_videos_per_day": 6})
    candidate = type(cfg).model_validate(candidate.model_dump())
    saved = {"version": version + 1, "selection": parsed.model_dump()}
    write_json(cfg.database_path.parent / "schedule-settings.json", saved)
    return candidate, saved
