"""Hosted narration with the approved voice, safe errors and reusable audio cache."""
import base64
import hashlib
import io
import json
import logging
import time

import httpx
import soundfile as sf

log = logging.getLogger(__name__)

STYLE = (
    "Natural Indian male technology presenter speaking conversational Hinglish. "
    "Warm, clear, energetic but relaxed. Natural Indian English pronunciation of English "
    "tech words with smooth Hindi transitions. Moderate pace, brief natural pauses, "
    "no shouting or exaggerated announcer delivery.")


def segment(cfg, text, path):
    from .audio import check_audio
    if not cfg.gemini_api_key.strip():
        raise RuntimeError("CONFIGURATION: save GEMINI_API_KEY in .env for Gemini narration")
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(json.dumps([cfg.gemini_tts_model, cfg.tts_voice, STYLE, text],
                                    ensure_ascii=False).encode()).hexdigest()
    cache = cfg.cache_dir / ("gemini-voice-" + key + ".wav")
    if cache.is_file():
        log.info("Reusing cached Gemini narration (%s)", cfg.tts_voice)
        check_audio(cache)
        path.write_bytes(cache.read_bytes())
        return
    body = {"model": cfg.gemini_tts_model,
            "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
                       "annotations": [{"type": "speech_metadata", "style": STYLE}]}]}],
            "response_format": {"type": "audio"},
            "generation_config": {"speech_config": [{"voice": cfg.tts_voice}]}}
    for attempt in range(3):
        try:
            log.info("Generating Gemini narration (%s), attempt %s", cfg.tts_voice, attempt + 1)
            response = httpx.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                                  headers={"x-goog-api-key": cfg.gemini_api_key}, json=body, timeout=180)
            response.raise_for_status()
            blocks = [part for step in response.json().get("steps", []) if step.get("type") == "model_output"
                      for part in step.get("content", []) if part.get("type") == "audio"]
            raw = base64.b64decode(blocks[-1]["data"], validate=True)
            samples, rate = sf.read(io.BytesIO(raw))
            temporary = cache.with_suffix(".tmp.wav")
            sf.write(temporary, samples, rate)
            stats = check_audio(temporary)
            if not .1 <= stats["duration"] <= 120:
                raise ValueError("Invalid speech duration")
            temporary.replace(cache)
            path.write_bytes(cache.read_bytes())
            return
        except httpx.HTTPStatusError as error:
            status = error.response.status_code
            if status == 429:
                try:
                    details = error.response.json().get("error", {}).get("details", [])
                    quotas = [v.get("quotaId", "") for d in details for v in d.get("violations", [])]
                    daily = any("perday" in q.lower().replace("_", "") for q in quotas)
                    delays = [d["retryDelay"].rstrip("s") for d in details if "retryDelay" in d]
                    delay = float(delays[0] if delays else error.response.headers.get("retry-after", "inf"))
                    if not daily and attempt < 2 and 0 <= delay < 60:
                        log.info("Gemini speech rate limit; waiting %.1fs before resuming", delay + 1)
                        time.sleep(min(60, delay + 1))
                        continue
                except (ValueError, KeyError, TypeError):
                    pass
                raise RuntimeError("TRANSIENT: Gemini speech quota reached; saved audio is retained for retry") from None
            if status >= 500 and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError("CONFIGURATION: Gemini speech rejected (HTTP " + str(status) +
                               "); check API key, model and voice access") from None
        except httpx.TransportError:
            if attempt == 2:
                raise RuntimeError("TRANSIENT: Gemini speech unavailable; retry uses saved audio") from None
            time.sleep(2 ** attempt)
        except (ValueError, KeyError, IndexError, TypeError, RuntimeError):
            raise ValueError("Gemini narration response failed audio validation") from None
