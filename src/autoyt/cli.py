import argparse
import importlib.util
import json
import logging
import platform
import shutil
import sys
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler

import httpx
import portalocker

from .config import Settings
from .pipeline import Pipeline, pipeline_lock
from .store import Store


def doctor(cfg):
    checks = {}
    checks["python"] = sys.version.split()[0]
    for binary in ("ffmpeg", "ffprobe"):
        from .audio import run
        try:
            run([getattr(cfg, binary), "-version"], timeout=15)
            checks[binary] = "OK"
        except Exception:
            checks[binary] = "MISSING: install FFmpeg including ffprobe; set absolute paths in .env"
    if cfg.llm_provider == "gemini":
        checks["llm"] = "Gemini key configured (not tested)" if cfg.gemini_api_key else "REQUIRED: GEMINI_API_KEY in .env"
    elif cfg.llm_provider == "groq":
        checks["llm"] = "Groq key configured (not tested)" if cfg.groq_api_key else "REQUIRED: GROQ_API_KEY in .env"
    else:
        try:
            response = httpx.get(cfg.ollama_base_url + "/api/tags", timeout=5)
            response.raise_for_status()
            names = {m["name"] for m in response.json()["models"]}
            checks["ollama"] = "OK" if cfg.ollama_model in names else "MODEL MISSING: ollama pull " + cfg.ollama_model
            checks["evidence_embeddings"] = "OK" if cfg.ollama_embedding_model in names else "MODEL MISSING: ollama pull " + cfg.ollama_embedding_model
        except Exception:
            checks["ollama"] = "UNAVAILABLE: install/start local Ollama"
    if cfg.tts_engine == "indicf5":
        ready = (cfg.indicf5_python.is_file() and (cfg.indicf5_model_dir / "provenance.json").is_file()
                 and cfg.indicf5_reference_audio.is_file() and cfg.indicf5_reference_text_file.is_file())
        checks["tts"] = "IndicF5 local CPU configured; run doctor --tts-test" if ready else "MISSING: see IndicF5 in SETUP.md"
        checks["voice"] = cfg.tts_voice + " / IndicF5 Hinglish"
    elif cfg.tts_engine == "elevenlabs":
        checks["elevenlabs_api_key"] = bool(cfg.elevenlabs_api_key.strip())
        checks["voice"] = cfg.tts_voice + " / eleven_flash_v2_5"
    elif cfg.tts_engine == "gemini":
        checks["tts"] = "Gemini speech key configured; run doctor --tts-test" if cfg.gemini_api_key else "REQUIRED: GEMINI_API_KEY"
        checks["voice"] = cfg.tts_voice + " / " + cfg.gemini_tts_model
    elif cfg.tts_engine == "kokoro":
        ready = importlib.util.find_spec("kokoro_onnx") and cfg.kokoro_model.is_file() and cfg.kokoro_voices.is_file()
        checks["tts"] = "Kokoro CPU installed; run doctor --tts-test" if ready else "MISSING: install .[tts] and run scripts/setup_kokoro.py"
        checks["voice"] = cfg.tts_voice + " / " + cfg.narration_language
    elif cfg.tts_engine == "piper":
        checks["tts"] = "OK" if shutil.which("piper") and cfg.piper_model.is_file() else "MISSING: Piper binary/model"
    else:
        checks["tts"] = "Windows SAPI; run doctor --tts-test to check English voice" if platform.system() == "Windows" else "UNAVAILABLE"
    cfg.prepare()
    for name, path in {"output": cfg.output_dir, "cache": cfg.cache_dir}.items():
        marker = path / ".write-check"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink()
        checks[name] = str(path.resolve())
    checks["database"] = str(Store(cfg.database_path).path.resolve())
    providers = [name for name, key in (("Pexels", cfg.pexels_api_key), ("Pixabay", cfg.pixabay_api_key)) if key]
    checks["footage"] = " + ".join(providers + ["local licensed assets", "original graphics"])
    checks["oauth"] = "token present (auth validity not tested)" if cfg.youtube_token_file.is_file() else "REQUIRED before upload: autoyt auth"
    checks["uploads_enabled"] = cfg.enable_upload
    checks["upload_visibility"] = cfg.youtube_privacy
    checks["schedule"] = cfg.schedule_times + " " + cfg.timezone + " (starts only with scheduler command)"
    checks["free_disk_gb"] = round(shutil.disk_usage(cfg.output_dir).free/1024**3, 2)
    return checks


def cleanup(cfg):
    """Remove only reproducible intermediates of terminal jobs; preserve review/recovery artifacts."""
    cutoff = datetime.now(timezone.utc)-timedelta(days=cfg.retention_days)
    removed = 0
    for job in Store(cfg.database_path).jobs(limit=None):
        if job["state"] not in ("AWAITING_REVIEW", "PUBLISHED", "SKIPPED", "FAILED"):
            continue
        if datetime.fromisoformat(job["updated_at"]) >= cutoff:
            continue
        folder = (cfg.output_dir/job["id"]).resolve()
        if not folder.is_relative_to(cfg.output_dir.resolve()) or not folder.is_dir():
            continue
        for pattern in ("clip-*.mp4", "scene-*.png", "voice-*.wav", "voice-*.txt"):
            for path in folder.glob(pattern):
                path.unlink()
                removed += 1
    return {"intermediate_files_removed": removed}


def main(argv=None):
    parser = argparse.ArgumentParser(prog="autoyt", description="Personal local technology Shorts pipeline")
    sub = parser.add_subparsers(dest="command", required=True)
    doc = sub.add_parser("doctor")
    doc.add_argument("--tts-test", action="store_true")
    dashboard = sub.add_parser("dashboard")
    dashboard.add_argument("--port", type=int, default=8765, choices=range(1024, 65536), metavar="PORT")
    for name in ("run-once", "generate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--dry-run", action="store_true")
        cmd.add_argument("--topic")
    for name in ("scheduler", "status", "jobs", "list-jobs", "trends", "auth", "cleanup", "plan-day"):
        sub.add_parser(name)
    for name in ("retry", "upload"):
        sub.add_parser(name).add_argument("job_id")
    args = parser.parse_args(argv)
    try:
        from .voice_settings import apply
        cfg = apply(Settings())
        from .render_settings import apply as apply_render
        from .script_settings import apply as apply_script
        cfg = apply_script(apply_render(cfg))
        cfg.prepare()
        handler = RotatingFileHandler("data/logs/autoyt.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
        logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), handler],
                            format="%(asctime)s %(levelname)s %(message)s")
        for noisy in ("httpx", "httpcore", "googleapiclient", "google.auth", "google_auth_oauthlib", "urllib3"):
            logging.getLogger(noisy).setLevel(logging.CRITICAL)
        result = None
        if args.command == "doctor":
            result = doctor(cfg)
            if args.tts_test:
                from .audio import Speech, check_audio
                path = cfg.cache_dir/"doctor-voice.wav"
                text = "नमस्कार! टेक ज्ञान में आपका स्वागत है। आज की तकनीक को समझें, आसान भाषा में।" if cfg.narration_language == "hi" else "Auto Y T is ready to explain technology clearly."
                Speech(cfg).segment(text, path)
                result["tts_actual_test"] = check_audio(path)
        elif args.command in ("generate", "run-once"):
            pipeline = Pipeline(cfg)
            job_id = pipeline.create_run(args.dry_run, args.topic)
            result = pipeline.store.get(job_id) if job_id else {"state": "NO_RUN"}
        elif args.command == "retry":
            pipeline = Pipeline(cfg)
            pipeline.retry(args.job_id)
            result = pipeline.store.get(args.job_id)
        elif args.command in ("status", "jobs", "list-jobs"):
            result = Store(cfg.database_path).jobs()
        elif args.command == "trends":
            from .discovery import discover
            from .net import Web
            result = [t.model_dump(mode="json") for t in discover(cfg, Web())]
        elif args.command == "plan-day":
            from .planning import prepare_day
            plan = prepare_day(cfg)
            result = {"date": plan["date"], "state": plan["state"], "ready_scripts": len(plan["entries"])}
        elif args.command == "scheduler":
            from .scheduling import serve
            serve(cfg)
        elif args.command == "dashboard":
            from .dashboard import serve
            serve(cfg, args.port)
        elif args.command == "auth":
            from dotenv import set_key

            from .youtube import service
            _, channel = service(cfg, interactive=True)
            if not cfg.youtube_channel_id:
                set_key(".env", "YOUTUBE_CHANNEL_ID", channel["id"])
            result = {"authorized_channel": channel["id"], "private_uploads_enabled": cfg.enable_upload,
                      "next": "After checking this channel, set ENABLE_UPLOAD=true in .env"}
        elif args.command == "upload":
            from .youtube import upload
            with pipeline_lock(cfg):
                store = Store(cfg.database_path)
                video_id = upload(cfg, store, args.job_id, cfg.output_dir/args.job_id)
            result = {"video_id": video_id, "privacy": store.load(args.job_id, "youtube")["privacy"]}
        elif args.command == "cleanup":
            with pipeline_lock(cfg):
                result = cleanup(cfg)
        if result is not None:
            print(json.dumps(result, indent=2, default=str))
        if isinstance(result, dict) and result.get("state") == "FAILED":
            return 1
        return 0
    except portalocker.exceptions.LockException:
        print("Another AutoYT process is active; no overlapping job started.", file=sys.stderr)
        return 2
    except Exception as error:
        # Config validation and remote exceptions may contain secrets; never print their repr.
        if type(error) in (ValueError, RuntimeError, FileNotFoundError):
            print(str(error), file=sys.stderr)
        else:
            print(f"{type(error).__name__}: check .env and run autoyt doctor; details withheld to protect credentials.",
                  file=sys.stderr)
        return 1
