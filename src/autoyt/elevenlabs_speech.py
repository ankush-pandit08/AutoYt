"""Official ElevenLabs Flash v2.5 adapter. Never retry a billable POST automatically."""

import hashlib
import json
import re
import time

import httpx
import numpy as np
import soundfile as sf

MODEL = "eleven_flash_v2_5"
BASE = "https://api.elevenlabs.io"
_catalog = {}


def headers(cfg):
    if not cfg.elevenlabs_api_key.strip():
        raise RuntimeError("CONFIGURATION: set ELEVENLABS_API_KEY in the server environment")
    return {"xi-api-key": cfg.elevenlabs_api_key}


def checked(response):
    if response.is_success:
        return response
    try:
        detail = response.json().get("detail", {})
        code = detail.get("status", "") if isinstance(detail, dict) else ""
    except (ValueError, TypeError):
        code = ""
    if code == "missing_permissions":
        message = "ElevenLabs key needs Voices: Read and Text to Speech permissions. Enable them in the API key settings"
    elif code in {"quota_exceeded", "insufficient_credits", "character_limit_exceeded"}:
        message = "ElevenLabs credits exhausted. Check your plan or wait for credits to reset"
    elif response.status_code == 429:
        message = "ElevenLabs rate limit reached. Wait before trying again"
    elif response.status_code in {401, 403}:
        message = "ElevenLabs access denied. Check the API key, permissions and voice access"
    elif response.status_code == 404:
        message = "ElevenLabs voice is unavailable. Refresh voices and choose an available voice"
    elif response.status_code >= 500:
        message = "ElevenLabs is temporarily unavailable. Try again later"
    else:
        message = "ElevenLabs rejected the request. Check model, voice and account access"
    # Never echo remote bodies, request headers, narration or credentials.
    raise RuntimeError(message + " (HTTP " + str(response.status_code) + "). No provider was switched")


def voices(cfg, page=None):
    auth = headers(cfg)
    if page is not None and (not isinstance(page, str) or len(page) > 2000):
        raise ValueError("Invalid voice page")
    key = (hashlib.sha256(cfg.elevenlabs_api_key.encode()).hexdigest(), page)
    cached = _catalog.get(key)
    if cached and time.monotonic() - cached[0] < 300:
        return cached[1]
    try:
        response = checked(
            httpx.get(
                BASE + "/v2/voices",
                headers=auth,
                params={"page_size": 100, **({"next_page_token": page} if page else {})},
                timeout=20,
            )
        )
        data = response.json()
        items = []
        for voice in data["voices"]:
            ident = voice["voice_id"]
            if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", ident):
                continue
            labels = voice.get("labels") or {}
            labels = {
                k: str(labels[k])[:80] for k in ("accent", "gender", "language", "description") if k in labels
            }
            preferred = labels.get("gender", "").lower() == "male" and any(
                term in (labels.get("accent", "") + " " + labels.get("language", "")).lower()
                for term in ("indian", "hindi")
            )
            items.append(
                {
                    "id": ident,
                    "name": str(voice.get("name") or ident)[:120],
                    "labels": labels,
                    "recommended": preferred,
                }
            )
        items.sort(key=lambda v: (not v["recommended"], v["name"].lower()))
        result = {"voices": items, "next_page": data.get("next_page_token") if data.get("has_more") else None}
    except httpx.TransportError:
        raise RuntimeError("ElevenLabs connection failed. Check connectivity and try again") from None
    except (ValueError, KeyError, TypeError):
        raise RuntimeError("ElevenLabs returned an invalid voice list. Try again later") from None
    if len(_catalog) > 30:
        _catalog.clear()
    _catalog[key] = (time.monotonic(), result)
    return result


def segment(cfg, text, path):
    from .audio import check_audio

    auth = headers(cfg)
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", cfg.tts_voice):
        raise ValueError("Choose an ElevenLabs voice from the available voices")
    if not text.strip() or len(text) > 5000:
        raise ValueError("ElevenLabs narration segment must contain 1 to 5000 characters")
    # Mixed-language narration stays multilingual. Saved scripts are never translated.
    language = (
        None if cfg.script_style == "hinglish" else ("hi" if re.search(r"[\u0900-\u097f]", text) else "en")
    )
    body = {"text": text, "model_id": MODEL, **({"language_code": language} if language else {})}
    key = hashlib.sha256(
        json.dumps(
            [
                MODEL,
                cfg.tts_voice,
                language,
                text,
                hashlib.sha256(cfg.elevenlabs_api_key.encode()).hexdigest(),
            ],
            ensure_ascii=False,
        ).encode()
    ).hexdigest()
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cfg.cache_dir / ("elevenlabs-voice-" + key + ".wav")
    if cache.is_file():
        check_audio(cache)
        path.write_bytes(cache.read_bytes())
        return
    try:
        response = checked(
            httpx.post(
                BASE + "/v1/text-to-speech/" + cfg.tts_voice,
                headers={**auth, "Accept": "audio/pcm"},
                params={"output_format": "pcm_24000"},
                json=body,
                timeout=90,
            )
        )
    except httpx.TransportError:
        raise RuntimeError(
            "ElevenLabs connection failed. No automatic retry was made to avoid duplicate credit use"
        ) from None
    raw = response.content
    if not raw or len(raw) % 2 or len(raw) > 24000 * 2 * 120:
        raise ValueError("ElevenLabs returned invalid PCM audio")
    temporary = cache.with_suffix(".tmp.wav")
    try:
        sf.write(temporary, np.frombuffer(raw, dtype="<i2"), 24000, subtype="PCM_16")
        stats = check_audio(temporary)
        if not 0.1 <= stats["duration"] <= 120:
            raise ValueError("ElevenLabs audio duration is invalid")
        temporary.replace(cache)
        path.write_bytes(cache.read_bytes())
    finally:
        temporary.unlink(missing_ok=True)
