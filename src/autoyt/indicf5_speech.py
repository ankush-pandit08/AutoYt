"""Local Hinglish narration in an isolated CPU worker, with exact-input caching."""
import hashlib
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path

from .store import digest, write_json

log = logging.getLogger(__name__)


def generate(cfg, items):
    from .audio import check_audio

    if not cfg.indicf5_python.is_file() or not (cfg.indicf5_model_dir / "provenance.json").is_file():
        raise RuntimeError("CONFIGURATION: IndicF5 runtime/model missing; see SETUP.md")
    if not cfg.indicf5_reference_audio.is_file() or not cfg.indicf5_reference_text_file.is_file():
        raise RuntimeError("CONFIGURATION: IndicF5 needs a reference WAV and its exact transcript; see SETUP.md")
    reference = cfg.indicf5_reference_text_file.read_text(encoding="utf-8").strip()
    if not reference:
        raise RuntimeError("CONFIGURATION: IndicF5 reference transcript is empty")
    stats = check_audio(cfg.indicf5_reference_audio)
    if not 1 <= stats["duration"] <= 10:
        raise ValueError("IndicF5 reference audio must be between one and ten seconds")
    cfg.cache_dir.mkdir(parents=True, exist_ok=True)
    identity = [digest(cfg.indicf5_model_dir / "provenance.json"),
                digest(cfg.indicf5_reference_audio), reference, cfg.indicf5_steps, cfg.tts_speed]
    missing, outputs = [], []
    for index, (text, path) in enumerate(items):
        key = hashlib.sha256(json.dumps([identity, text], ensure_ascii=False).encode()).hexdigest()
        cached = cfg.cache_dir / ("indicf5-voice-" + key + ".wav")
        outputs.append((cached, path))
        if cached.is_file():
            check_audio(cached)
        else:
            missing.append({"index": index, "text": text, "output": str(cached.resolve())})
    if missing:
        request_path = cfg.cache_dir / ("indicf5-request-" + uuid.uuid4().hex + ".json")
        write_json(request_path, {"model_dir": str(cfg.indicf5_model_dir.resolve()),
            "reference_audio": str(cfg.indicf5_reference_audio.resolve()), "reference_text": reference,
            "steps": cfg.indicf5_steps, "speed": cfg.tts_speed, "threads": cfg.indicf5_threads,
            "metrics_path": str((cfg.cache_dir / "indicf5-last-run.json").resolve()), "items": missing})
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1", "PYTHONUTF8": "1"})
        log.info("Generating %s local IndicF5 segments (%s CPU threads, %s steps); inference may take minutes",
                 len(missing), cfg.indicf5_threads, cfg.indicf5_steps)
        try:
            error_log = cfg.cache_dir / "indicf5-worker-error.log"
            with error_log.open("w", encoding="utf-8") as stream:
                result = subprocess.run([str(cfg.indicf5_python.resolve()),
                    str(Path(__file__).with_name("indicf5_worker.py")), str(request_path.resolve())],
                    env=env, timeout=cfg.indicf5_timeout_seconds, stdout=stream,
                    stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace")
            if result.returncode:
                with error_log.open("a", encoding="utf-8") as stream:
                    stream.write(f"\nWorker exit code: {result.returncode}\n")
                if "paging file is too small" in error_log.read_text(encoding="utf-8").lower():
                    raise RuntimeError("CONFIGURATION: IndicF5 ran out of Windows memory; close heavy apps "
                                       "or use a machine with more RAM. No Gemini fallback was used")
                raise RuntimeError("CONFIGURATION: IndicF5 local inference failed; check its runtime and model setup")
        except subprocess.TimeoutExpired:
            raise RuntimeError("TRANSIENT: IndicF5 CPU inference timed out; saved audio is retained for retry") from None
        finally:
            request_path.unlink(missing_ok=True)
    for cached, path in outputs:
        stats = check_audio(cached)
        if not .1 <= stats["duration"] <= 120:
            raise ValueError("IndicF5 narration duration outside safe limits")
        path.write_bytes(cached.read_bytes())


def segment(cfg, text, path):
    generate(cfg, [(text, path)])
