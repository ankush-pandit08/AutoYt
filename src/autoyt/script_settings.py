"""Explicit script-provider selection. Keys stay exclusively in server configuration."""
import json
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, model_validator

from .store import write_json

LOCAL_MODELS = ("gemma3:4b", "qwen3:8b")


class ScriptSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["groq", "gemini", "openrouter", "ollama"]
    model: str | None = None

    @model_validator(mode="after")
    def local_model(self):
        if self.model is not None and (self.provider != "ollama" or self.model not in LOCAL_MODELS):
            raise ValueError("Choose Gemma 3 4B or Qwen 3 8B for local generation")
        return self


def record(cfg):
    path = cfg.database_path.parent / "script-settings.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {
        "version": 0, "selection": {"provider": cfg.llm_provider, **(
            {"model": cfg.ollama_model} if cfg.llm_provider == "ollama" else {})}}


def apply(cfg):
    path = cfg.database_path.parent / "script-settings.json"
    if not path.is_file():
        return cfg
    parsed = ScriptSelection.model_validate(record(cfg)["selection"])
    return cfg.model_copy(update={"llm_provider": parsed.provider, **(
        {"ollama_model": parsed.model} if parsed.model else {})})


def snapshot(cfg):
    model = getattr(cfg, cfg.llm_provider + "_model")
    return {"provider": cfg.llm_provider, "model": model}


def freeze(cfg, value):
    provider = value.get("provider")
    if provider not in {"groq", "gemini", "ollama", "openrouter"} or not isinstance(value.get("model"), str):
        raise ValueError("Saved script provider is invalid")
    model_key = provider + "_model"
    return cfg.model_copy(update={"llm_provider": provider, model_key: value["model"]})


def save(cfg, value, version):
    parsed = ScriptSelection.model_validate(value)
    if type(version) is not int or record(cfg)["version"] != version:
        raise ValueError("Script settings changed. Reload before saving again")
    if parsed.provider == "ollama":
        if parsed.model not in LOCAL_MODELS:
            raise ValueError("Choose a local script model")
        try:
            response = httpx.get(cfg.ollama_base_url + "/api/tags", timeout=3)
            response.raise_for_status()
            installed = {m["name"] for m in response.json()["models"]}
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            raise ValueError("Start Ollama before saving a local script model") from None
        if parsed.model not in installed:
            raise ValueError("Download this model first: ollama pull " + parsed.model)
    elif not getattr(cfg, parsed.provider+"_api_key").strip():
        raise ValueError("Set "+parsed.provider.upper()+"_API_KEY in the local environment before selecting this provider")
    saved = {"version": version+1, "selection": parsed.model_dump(exclude_none=True)}
    write_json(cfg.database_path.parent / "script-settings.json", saved)
    return cfg.model_copy(update={"llm_provider": parsed.provider, **(
        {"ollama_model": parsed.model} if parsed.model else {})}), saved
