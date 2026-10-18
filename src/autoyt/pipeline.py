import logging
import shutil
from contextlib import contextmanager

import portalocker

from . import ai, audio, discovery, licensing, media, research, video, youtube
from .models import Research, Script, Topic
from .net import Web
from .store import Store, digest, write_json

log = logging.getLogger(__name__)
STAGES = ["DISCOVERING", "RESEARCHING", "SCRIPTING", "SYNTHESIZING_AUDIO", "ACQUIRING_MEDIA",
          "RENDERING", "VALIDATING"]


@contextmanager
def pipeline_lock(cfg):
    with portalocker.Lock(str(cfg.database_path) + ".pipeline.lock", timeout=0):
        yield


class Pipeline:
    def __init__(self, cfg):
        self.cfg = cfg
        cfg.prepare()
        self.store = Store(cfg.database_path)
        self.web = Web()
        self.llm = ai.LLM(cfg)

    def create_run(self, dry_run=False, topic=None, slot=None):
        with pipeline_lock(self.cfg):
            # Interrupted active work takes priority over creating another job.
            active = [j for j in self.store.jobs() if j["state"] in STAGES + ["CREATED", "UPLOADING"]]
            if active:
                if bool(active[-1]["dry_run"]) != dry_run:
                    raise ValueError("An interrupted job has a different dry-run mode; retry its ID first")
                return self.execute(active[-1]["id"])
            job_id = self.store.create(dry_run, topic, slot, self.cfg.max_videos_per_day, self.cfg.timezone)
            if job_id:
                return self.execute(job_id)
            log.info("Slot already handled or daily limit reached")

    def retry(self, job_id):
        with pipeline_lock(self.cfg):
            job = self.store.get(job_id)
            revision_path = self.cfg.output_dir / job_id / "script_revision.json"
            previous = self.store.load(job_id, "script")
            if job["stage"] == 3 and previous and revision_path.is_file():
                revision = ai.read_revision(revision_path.read_text(encoding="utf-8"))
                changed = revision.narration != [s["narration"] for s in previous["scenes"]]
                changed |= any(getattr(revision, k) != previous[k] for k in ("title", "description", "thumbnail_text"))
                if isinstance(revision, ai.EditorialDraft):
                    changed |= revision.headlines != [s["headline"] for s in previous["scenes"]]
                if changed:
                    # A changed editorial revision must pass scripting checks again before new audio.
                    self.store.update(job_id, job["state"], stage=2)
            self.store.update(job_id, job["state"], retries=job["retries"]+1, error=None)
            return self.execute(job_id)

    def _save(self, job_id, folder, name, value):
        if hasattr(value, "model_dump"):
            value = value.model_dump(mode="json")
        if name == "script":
            versions = self.store.load(job_id, "script_versions") or []
            if not versions or versions[-1] != value:
                versions.append(value)
                write_json(folder / "script_versions.json", versions)
                self.store.save(job_id, "script_versions", versions)
        write_json(folder / (name + ".json"), value)
        self.store.save(job_id, name, value)

    def execute(self, job_id):
        cfg, db = self.cfg, self.store
        job = db.get(job_id)
        if job["state"] in ("AWAITING_REVIEW", "PUBLISHED"):
            return job_id
        folder = cfg.output_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if shutil.disk_usage(folder).free < cfg.min_free_gb * 1024**3:
                raise RuntimeError("CONFIGURATION: insufficient disk space")
            start = job["stage"]
            for index in range(start, len(STAGES)):
                db.update(job_id, STAGES[index], error=None)
                log.info("Job %s: %s", job_id, STAGES[index])
                if index == 0:
                    saved = db.load(job_id, "topic")
                    if saved:
                        candidates = [Topic.model_validate(saved)]
                    else:
                        candidates = discovery.discover(cfg, self.web)
                        previous = db.recent_topics(cfg.topic_dedup_days)
                        candidates = [t for t in candidates if not discovery.duplicate(t.title, previous)]
                        if job["topic"]:
                            candidates = [t for t in candidates if discovery.similarity(t.title, job["topic"]) >= .35]
                    if not candidates:
                        db.update(job_id, "SKIPPED", error="No fresh, relevant, nonduplicate technology topic")
                        return job_id
                    if not saved:
                        self._save(job_id, folder, "candidates", [t.model_dump(mode="json") for t in candidates])
                    self._save(job_id, folder, "topic", candidates[0])
                    # A topic is reserved before research; failed stories do not repeatedly consume slots.
                    db.reserve_topic(job_id, candidates[0].title, discovery.fingerprint(candidates[0].title))
                    db.update(job_id, STAGES[index], topic=candidates[0].title)
                elif index == 1:
                    candidates = [Topic.model_validate(t) for t in db.load(job_id, "candidates")]
                    result = research.research(cfg, self.web, self.llm,
                                               Topic.model_validate(db.load(job_id, "topic")), candidates, folder)
                    self._save(job_id, folder, "research", result)
                elif index == 2:
                    revision_path = folder / "script_revision.json"
                    revision = {}
                    if revision_path.is_file():
                        revision["reviewed_draft"] = ai.read_revision(revision_path.read_text(encoding="utf-8"))
                        log.info("Using local editorial revision; all evidence checks still apply")
                    script = ai.generate(cfg, self.llm, Topic.model_validate(db.load(job_id, "topic")),
                                         Research.model_validate(db.load(job_id, "research")), **revision)
                    for old in db.recent_scripts(cfg.topic_dedup_days, job_id):
                        if discovery.duplicate(script.title, [old["title"]]) or \
                                discovery.similarity(script.script, old["script"]) > .92:
                            raise ValueError("Script/title too similar to a recent generated video")
                    self._save(job_id, folder, "script", script)
                elif index == 3:
                    script = Script.model_validate(db.load(job_id, "script"))
                    speech = audio.Speech(cfg)
                    for revision in range(2):
                        segments = speech.synthesize(script, folder)
                        duration = segments[-1]["end"]
                        if cfg.duration_min <= duration <= cfg.duration_max:
                            break
                        if (folder / "script_revision.json").is_file():
                            db.update(job_id, STAGES[index], stage=2)
                            raise ValueError(f"Reviewed narration measured {duration:.1f}s; revise "
                                             "script_revision.json to fit the configured duration and retry")
                        if revision == 1:
                            raise ValueError("Narration outside duration tolerance after one revision")
                        script = ai.generate(cfg, self.llm, Topic.model_validate(db.load(job_id, "topic")),
                                             Research.model_validate(db.load(job_id, "research")),
                                             f"Measured {duration:.1f}s; target {cfg.video_duration_seconds}s. "
                                             "Adjust word count within 90–115 words, preserve verified facts.")
                        self._save(job_id, folder, "script", script)
                    self._save(job_id, folder, "timing", segments)
                elif index == 4:
                    script = Script.model_validate(db.load(job_id, "script"))
                    self._save(job_id, folder, "media_assets", media.choose_media(cfg, self.web, script))
                elif index == 5:
                    licensing.audit(db.load(job_id, "media_assets"))
                    script = Script.model_validate(db.load(job_id, "script"))
                    timings = video.subtitles(cfg, db.load(job_id, "timing"), folder)
                    self._save(job_id, folder, "caption_timing", timings)
                    video.render(cfg, script, db.load(job_id, "timing"), db.load(job_id, "media_assets"), folder)
                    self._save(job_id, folder, "metadata", youtube.metadata(script, job_id,
                                                                            db.load(job_id, "media_assets"),
                                                                            cfg.youtube_privacy))
                elif index == 6:
                    self._save(job_id, folder, "media_license_audit", licensing.audit(db.load(job_id, "media_assets")))
                    script = Script.model_validate(db.load(job_id, "script"))
                    evidence = Research.model_validate(db.load(job_id, "research"))
                    research.verify_claims(evidence.claims, research.independent_sources(evidence.sources))
                    ai.validate_grounding(script, evidence)
                    result = video.quality(cfg, folder / "final.mp4", db.load(job_id, "caption_timing"),
                                           folder / "narration.wav")
                    self._save(job_id, folder, "validation", result)
                    db.update(job_id, "VALIDATING", content_hash=digest(folder / "final.mp4"))
                db.update(job_id, STAGES[index], stage=index+1)
            db.update(job_id, "READY_TO_UPLOAD")
            if cfg.enable_upload and not job["dry_run"]:
                youtube.upload(cfg, db, job_id, folder)
            return job_id
        except Exception as error:
            # Third-party exception messages may contain headers or tokens; persist only safe categories.
            category = "CONFIGURATION" if isinstance(error, (FileNotFoundError, ImportError)) else "VALIDATION"
            if isinstance(error, RuntimeError):
                prefix = str(error).split(":", 1)[0]
                category = prefix if prefix in ("TRANSIENT", "CONFIGURATION", "PERMANENT") else "PERMANENT"
            safe = f"{category}: {type(error).__name__}; stage {db.get(job_id)['stage']}. "
            # Our own ValueErrors contain quality diagnostics only.
            if isinstance(error, ValueError) and type(error).__module__ == "builtins":
                safe += str(error)[:300]
            elif type(error) is RuntimeError and str(error).split(":", 1)[0] in (
                    "CONFIGURATION", "TRANSIENT", "PERMANENT"):
                safe += str(error)[:300]
            else:
                safe += "Run doctor and consult SETUP.md; external exception details suppressed."
            db.update(job_id, "FAILED", error=safe)
            log.error("Job %s: %s", job_id, safe)
            return job_id
