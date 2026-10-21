"""Explicit script-provider selection. Keys stay exclusively in server configuration."""
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict

from .store import write_json


class ScriptSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["groq", "gemini"]


def record(cfg):
    path = cfg.database_path.parent / "script-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "version": 0, "selection": {"provider": cfg.llm_provider}}


def apply(cfg):
    path = cfg.database_path.parent / "script-settings.json"
    return cfg.model_copy(update={"llm_provider": ScriptSelection.model_validate(
        record(cfg)["selection"]).provider}) if path.is_file() else cfg


def snapshot(cfg):
    model = {"groq": cfg.groq_model, "gemini": cfg.gemini_model, "ollama": cfg.ollama_model}[cfg.llm_provider]
    return {"provider": cfg.llm_provider, "model": model}


def freeze(cfg, value):
    provider = value.get("provider")
    if provider not in {"groq", "gemini", "ollama"} or not isinstance(value.get("model"), str):
        raise ValueError("Saved script provider is invalid")
    model_key = {"groq": "groq_model", "gemini": "gemini_model", "ollama": "ollama_model"}[provider]
    return cfg.model_copy(update={"llm_provider": provider, model_key: value["model"]})


def save(cfg, value, version):
    parsed = ScriptSelection.model_validate(value)
    if type(version) is not int or record(cfg)["version"] != version:
        raise ValueError("Script settings changed. Reload before saving again")
    if not getattr(cfg, parsed.provider+"_api_key").strip():
        raise ValueError("Set "+parsed.provider.upper()+"_API_KEY in the local environment before selecting this provider")
    saved = {"version": version+1, "selection": parsed.model_dump()}
    write_json(cfg.database_path.parent / "script-settings.json", saved)
    return cfg.model_copy(update={"llm_provider": parsed.provider}), saved
