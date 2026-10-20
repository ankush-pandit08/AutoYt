"""Non-secret voice preferences shared by dashboard, CLI and scheduled pipelines."""

import json
import re

from .store import write_json

ENGINES = {"gemini", "elevenlabs", "kokoro", "piper", "sapi", "indicf5"}


def path(cfg):
    return cfg.database_path.parent / "voice-settings.json"


def selection(cfg):
    return {
        "provider": cfg.tts_engine,
        "voice": cfg.tts_voice,
        "language": "hinglish"
        if cfg.narration_language == "hi" and cfg.script_style == "hinglish"
        else cfg.narration_language,
    }


def record(cfg):
    return (
        json.loads(path(cfg).read_text(encoding="utf-8"))
        if path(cfg).is_file()
        else {"version": 0, "selection": selection(cfg), "voices": {cfg.tts_engine: cfg.tts_voice}}
    )


def validate(cfg, value):
    if not isinstance(value, dict) or set(value) != {"provider", "voice", "language"}:
        raise ValueError("Choose a voice provider, voice and script language")
    if value["provider"] not in ENGINES or value["language"] not in {"en", "hi", "hinglish"}:
        raise ValueError("Choose an available provider and script language")
    if not isinstance(value["voice"], str) or not re.fullmatch(r"[A-Za-z0-9_ .-]{1,80}", value["voice"]):
        raise ValueError("Choose a valid voice")
    if value["provider"] == "elevenlabs" and not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value["voice"]):
        raise ValueError("Choose an ElevenLabs voice from the voice list")
    if value["provider"] == "kokoro" and value["language"] != "en" and not value["voice"].startswith("h"):
        raise ValueError("Hindi or Hinglish with Kokoro requires a Hindi voice, such as hm_omega")
    return type(cfg).model_validate(
        {
            **cfg.model_dump(),
            "tts_engine": value["provider"],
            "tts_voice": value["voice"],
            "narration_language": "en" if value["language"] == "en" else "hi",
            "script_style": "hinglish" if value["language"] == "hinglish" else "standard",
        }
    )


def apply(cfg):
    return validate(cfg, record(cfg)["selection"]) if path(cfg).is_file() else cfg


def save(cfg, value, version):
    saved = record(cfg)
    if type(version) is not int or version != saved["version"]:
        raise ValueError("Voice settings changed. Reload before saving again")
    updated = validate(cfg, value)
    if updated.tts_engine == "elevenlabs" and not updated.elevenlabs_api_key.strip():
        raise ValueError("Set ELEVENLABS_API_KEY on the server before selecting ElevenLabs")
    if updated.tts_engine == "gemini" and not updated.gemini_api_key.strip():
        raise ValueError("Set GEMINI_API_KEY on the server before selecting Gemini")
    saved = {
        "version": saved["version"] + 1,
        "selection": selection(updated),
        "voices": {**saved["voices"], updated.tts_engine: updated.tts_voice},
    }
    write_json(path(cfg), saved)
    return updated, saved
