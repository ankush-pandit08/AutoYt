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
    if cfg.llm_provider == "openrouter":
        checks["llm"] = "OpenRouter free-only key configured (not tested)" if cfg.openrouter_api_key else "REQUIRED: OPENROUTER_API_KEY in .env"
    elif cfg.llm_provider == "gemini":
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


def _podcast_commands(sub):
    """Register 'podcast' subcommand group and its sub-subcommands."""
    podcast = sub.add_parser("podcast", help="Podcast clipping platform commands")
    psub = podcast.add_subparsers(dest="podcast_command", required=True)

    # podcast sources
    psub.add_parser("sources", help="List configured podcast sources")

    # podcast discover
    disc = psub.add_parser("discover", help="Discover new episodes from all enabled sources")
    disc.add_argument("--source", dest="source_id", default=None,
                      help="Limit discovery to a single source ID")

    # podcast episodes
    eps = psub.add_parser("episodes", help="List discovered podcast episodes")
    eps.add_argument("--status", default=None,
                     help="Filter by status (discovered|authorized|processing|processed|skipped|failed)")
    eps.add_argument("--source", dest="source_id", default=None,
                     help="Filter by source ID")

    # podcast authorize
    auth = psub.add_parser("authorize", help="Grant processing authorisation for an episode")
    auth.add_argument("episode_id", help="Episode ID (source_id:video_id)")
    auth.add_argument("--basis", required=True,
                      choices=["creator_permission", "cc_license", "public_domain",
                               "fair_use_claimed", "other"],
                      help="Rights basis for authorisation")
    auth.add_argument("--evidence-url", default=None,
                      help="URL to permission email or license page")
    auth.add_argument("--notes", default=None,
                      help="Optional free-form notes")

    # podcast clips
    clips = psub.add_parser("clips", help="List podcast clips")
    clips.add_argument("--episode", dest="episode_id", default=None,
                       help="Filter by episode ID")
    clips.add_argument("--status", default=None,
                       help="Filter by status (candidate|approved|rejected|rendered|uploaded|failed)")

    # podcast process
    proc = psub.add_parser("process", help="Process an authorized episode into candidate clips")
    proc.add_argument("episode_id", help="Episode ID (source_id:video_id)")
    proc.add_argument("--clips", type=int, default=4, help="Maximum number of clips to extract")
    proc.add_argument("--style", choices=["minimal", "dynamic", "bold"], default="dynamic",
                      help="Subtitle style")

    # podcast approve
    appr = psub.add_parser("approve", help="Approve a clip for YouTube upload")
    appr.add_argument("clip_id", help="Clip ID (episode_id:clip_index)")

    # podcast reject
    rej = psub.add_parser("reject", help="Reject a clip")
    rej.add_argument("clip_id", help="Clip ID (episode_id:clip_index)")
    rej.add_argument("--reason", default=None, help="Reason for rejection")

    # podcast upload
    upl = psub.add_parser("upload", help="Upload an approved clip to YouTube (private review mode)")
    upl.add_argument("clip_id", help="Clip ID to upload")
    upl.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private",
                     help="YouTube privacy setting (default: private)")

    # podcast publish-next
    pub = psub.add_parser("publish-next", help="Publish next ready Short in queue (used by daily 7pm-9pm automated scheduler)")
    pub.add_argument("--privacy", choices=["private", "unlisted", "public"], default="private",
                     help="YouTube privacy setting (default: private)")
    pub.add_argument("--slot", type=int, default=1, help="Slot index (1-5 for evening window)")

    # podcast scheduler
    psub.add_parser("scheduler", help="Run foreground podcast scheduler (08:00, 14:00, 18:00 prep, 19:00-21:00 5-Shorts publish IST)")

    return podcast


def _run_podcast(args, cfg):
    """Execute the requested podcast sub-command."""
    from .podcast.sources import SourceRegistry
    from .store import Store
    store = Store(cfg.database_path)

    if args.podcast_command == "sources":
        registry = SourceRegistry()
        return {
            "sources": [
                {
                    "id": s.id,
                    "name": s.name,
                    "show_name": s.show_name,
                    # Channel IDs are placeholders — verify before use (see sources.py TODO)
                    "youtube_channel_id": s.youtube_channel_id,
                    "enabled": s.enabled,
                    "priority_topics": s.priority_topics,
                }
                for s in registry.all()
            ]
        }

    if args.podcast_command == "discover":
        from .podcast.discovery import EpisodeDiscovery
        registry = SourceRegistry()
        discovery = EpisodeDiscovery(cfg, store)

        if getattr(args, "source_id", None):
            source = registry.get(args.source_id)
            if not source:
                raise ValueError(f"Source '{args.source_id}' not found in registry")
            episodes = discovery.discover_episodes(source)
        else:
            episodes = discovery.discover_all_sources(registry)

        # Save to store
        saved = 0
        for ep in episodes:
            existing = store.get_podcast_episode(ep.id)
            if existing is None:
                store.save_podcast_episode({
                    **ep.to_dict(),
                    "status": "discovered",
                })
                saved += 1

        return {
            "discovered": len(episodes),
            "new_saved": saved,
            "episodes": [ep.to_dict() for ep in episodes[:20]],
        }

    if args.podcast_command == "episodes":
        episodes = store.list_podcast_episodes(
            status=getattr(args, "status", None),
            source_id=getattr(args, "source_id", None),
        )
        return {"count": len(episodes), "episodes": episodes}

    if args.podcast_command == "authorize":
        from .podcast.rights import RightsChecker
        checker = RightsChecker(store)
        record = checker.grant_authorization(
            args.episode_id,
            args.basis,
            evidence_url=args.evidence_url,
            notes=args.notes,
        )
        return record.to_dict()

    if args.podcast_command == "clips":
        clips = store.list_podcast_clips(
            episode_id=getattr(args, "episode_id", None),
            status=getattr(args, "status", None),
        )
        return {"count": len(clips), "clips": clips}

    if args.podcast_command == "process":
        from .podcast.pipeline import PodcastPipeline
        pipeline = PodcastPipeline(cfg, store)
        results = pipeline.process_episode(
            args.episode_id,
            max_clips=getattr(args, "clips", 4),
            subtitle_style=getattr(args, "style", "dynamic"),
        )
        return {
            "episode_id": args.episode_id,
            "clips_generated": len(results),
            "clips": [
                {
                    "clip_id": r.clip_id,
                    "title": r.title,
                    "score": r.score,
                    "duration": r.duration,
                    "video": str(r.video_path),
                    "thumbnail": str(r.thumbnail_path),
                }
                for r in results
            ],
        }

    if args.podcast_command == "approve":
        clip = store.get_podcast_clip(args.clip_id)
        if not clip:
            raise ValueError(f"Clip '{args.clip_id}' not found")
        store.update_clip_status(args.clip_id, "approved")
        return {"clip_id": args.clip_id, "status": "approved"}

    if args.podcast_command == "reject":
        clip = store.get_podcast_clip(args.clip_id)
        if not clip:
            raise ValueError(f"Clip '{args.clip_id}' not found")
        store.update_clip_status(
            args.clip_id, "rejected", review_notes=getattr(args, "reason", None)
        )
        return {"clip_id": args.clip_id, "status": "rejected"}

    if args.podcast_command == "upload":
        from .podcast.publisher import PodcastPublisher
        publisher = PodcastPublisher(cfg, store)
        return publisher.upload_clip(args.clip_id, privacy=getattr(args, "privacy", "private"))

    if args.podcast_command == "publish-next":
        from .podcast.scheduler import PodcastScheduler
        sched = PodcastScheduler(cfg, store)
        return sched.job_publish_scheduled_short(
            slot_number=getattr(args, "slot", 1),
            privacy=getattr(args, "privacy", "private")
        )

    if args.podcast_command == "scheduler":
        import time

        from .podcast.scheduler import PodcastScheduler
        sched = PodcastScheduler(cfg, store)
        sched.start()
        print("Podcast scheduler running (Asia/Kolkata: 08:00, 14:00, 18:00 prep, 19:00-21:00 5-Shorts publish IST). Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            sched.shutdown()
        return None

    raise ValueError(f"Unknown podcast command: {args.podcast_command}")


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
    _podcast_commands(sub)
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
        elif args.command == "podcast":
            result = _run_podcast(args, cfg)
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
        if type(error) in (ValueError, RuntimeError, FileNotFoundError, PermissionError):
            print(str(error), file=sys.stderr)
        else:
            print(f"{type(error).__name__}: check .env and run autoyt doctor; details withheld to protect credentials.",
                  file=sys.stderr)
        return 1
