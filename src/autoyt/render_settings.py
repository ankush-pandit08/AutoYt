"""Non-secret rendering defaults; individual jobs keep their own immutable selection."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, StrictBool

from .store import write_json


class RenderSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    render_mode: Literal["normal", "motion_graphics", "hybrid"] = "normal"
    render_fallback: StrictBool = True
    motion_icons: StrictBool = True
    motion_lottie: StrictBool = True
    sound_effects: StrictBool = True


def selection(cfg):
    return RenderSelection(**{key: getattr(cfg, key) for key in RenderSelection.model_fields}).model_dump()


def record(cfg):
    path = cfg.database_path.parent / "render-settings.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"version": 0, "selection": selection(cfg)}
    saved["selection"] = RenderSelection.model_validate(saved["selection"]).model_dump()
    return saved


def apply(cfg):
    return cfg.model_copy(update=RenderSelection.model_validate(record(cfg)["selection"]).model_dump())


def save(cfg, value, version):
    current = record(cfg)
    if type(version) is not int or version != current["version"]:
        raise ValueError("Rendering defaults changed. Reload before saving again")
    parsed = RenderSelection.model_validate(value)
    saved = {"version": version+1, "selection": parsed.model_dump()}
    write_json(cfg.database_path.parent / "render-settings.json", saved)
    return cfg.model_copy(update=parsed.model_dump()), saved
