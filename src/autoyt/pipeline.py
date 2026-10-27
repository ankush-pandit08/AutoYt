import json
import logging
import shutil
from contextlib import contextmanager
from datetime import datetime, timezone

import portalocker

from . import (
    ai,
    audio,
    discovery,
    licensing,
    media,
    narration_duration,
    research,
    video,
    youtube,
    youtube_content,
)
from .limits import research_budget, research_duration
from .manual import ManualRequest, find_candidates
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
        from .schedule_settings import apply as apply_schedule
        cfg = apply_schedule(cfg)
        from .voice_settings import apply
        cfg = apply(cfg)
        from .render_settings import apply as apply_render
        cfg = apply_render(cfg)
        from .script_settings import apply as apply_script
        cfg = apply_script(cfg)
        self.cfg = cfg
        cfg.prepare()
        self.store = Store(cfg.database_path)
        self.web = Web()
        self.llm = ai.LLM(cfg)

    def create_run(self, dry_run=False, topic=None, slot=None, prepared=None, manual_input=None, fresh=False, render_mode=None):
        from .render_settings import RenderSelection, selection
        render_selection = RenderSelection.model_validate({**selection(self.cfg), **(
            {"render_mode": render_mode} if render_mode is not None else {})}).model_dump()
        request = ManualRequest.model_validate(manual_input) if manual_input is not None else None
        if request and (slot or prepared or topic):
            raise ValueError("A custom request must be a separate manual run")
        with pipeline_lock(self.cfg):
            if prepared:
                selected = Topic.model_validate(prepared["topic"])
                script = Script.model_validate(prepared["script"])
                if prepared.get("source_brief"):
                    source = youtube_content.SourceBrief.model_validate(prepared["source_brief"])
                    from .topic_research import evidence_basis, verify_evidence
                    if source.evidence:
                        verify_evidence(source.evidence)
                    if script.source_basis != (evidence_basis(source.evidence) if source.evidence else source.basis) or (source.basis in {"youtube", "google_news"} and not source.evidence) or (source.basis == "youtube" and not source.videos):
                        raise ValueError("Prepared script does not match its saved source basis")
                else:
                    evidence = Research.model_validate(prepared["research"])
                    if self.cfg.content_mode != "youtube_insights":
                        research.verify_claims(evidence.claims, research.independent_sources(evidence.sources))
                        ai.validate_grounding(script, evidence)
            # Interrupted active work takes priority over creating another job.
            active = [j for j in self.store.jobs(limit=None) if j["state"] in STAGES + ["CREATED", "UPLOADING"]]
            if active:
                if fresh:
                    for interrupted in active:
                        attempt = self.store.attempt(interrupted["id"])
                        if attempt and (attempt["video_id"] or attempt["session_uri"] or attempt["state"] != "REJECTED"):
                            raise ValueError("An upload needs reconciliation. Retry its ID before creating a fresh run")
                    # Holding the pipeline lock proves these rows have no running worker.
                    # Preserve their history while allowing the explicit fresh-run request.
                    for interrupted in active:
                        self.store.update(interrupted["id"], "FAILED",
                            error="Interrupted work retained in history; a fresh manual run was requested")
                else:
                    if request:
                        raise ValueError("Retry or finish the interrupted run before starting your custom input")
                    if bool(active[-1]["dry_run"]) != dry_run:
                        raise ValueError("An interrupted job has a different dry-run mode; retry its ID first")
                    return self.execute(active[-1]["id"])
            job_id = self.store.create(dry_run, request.topic if request else topic, slot,
                                       self.cfg.max_videos_per_day, self.cfg.timezone)
            if job_id:
                self.store.save(job_id, "render_settings", render_selection)
                from .script_settings import snapshot as script_snapshot
                self.store.save(job_id, "script_settings", script_snapshot(self.cfg))
                self.store.save(job_id, "request", {"topic_filter": request.topic if request else topic,
                    "manual_input": request.model_dump() if request else None, "fresh": fresh})
                if prepared:
                    folder = self.cfg.output_dir / job_id
                    folder.mkdir(parents=True, exist_ok=True)
                    if slot:
                        self._save(job_id, folder, "morning_plan", {"date": slot[:10]})
                    if not discovery.exclude_rejected([selected], self.store.rejected_topic_history(job_id)):
                        self._save(job_id, folder, "topic", selected)
                        self.store.update(job_id, "SKIPPED", topic=selected.title,
                                          error="This planned topic previously skipped or failed. Choose a new topic.")
                        return job_id
                    if discovery.duplicate(selected.title, self.store.recent_topics(
                            self.cfg.topic_dedup_days, self.cfg.youtube_privacy)) or (not prepared.get("source_brief") and discovery.repeated_insight(
                                [c.claim for c in evidence.claims], self.store.recent_research(
                                    self.cfg.topic_dedup_days, job_id, self.cfg.youtube_privacy))):
                        self.store.update(job_id, "SKIPPED", error="Morning story already covered")
                        return job_id
                    self.store.reserve_topic(job_id, selected.title, discovery.fingerprint(selected.title),
                                             self.cfg.youtube_privacy, self.cfg.topic_dedup_days)
                    source_artifact = ("source_brief", source) if prepared.get("source_brief") else ("research", evidence)
                    for name, value in (("topic", selected), source_artifact, ("script", script)):
                        self._save(job_id, folder, name, value)
                    self.store.update(job_id, "SCRIPTING", stage=3, topic=selected.title)
                return self.execute(job_id)
            log.info("Slot already handled or daily limit reached")

    def retry(self, job_id):
        with pipeline_lock(self.cfg):
            job = self.store.get(job_id)
            attempt = self.store.attempt(job_id)
            if job["stage"] >= 4 and not (self.cfg.output_dir / job_id / "final.mp4").is_file():
                if attempt and (attempt["video_id"] or attempt["session_uri"] or attempt["state"] != "REJECTED"):
                    # Never regenerate different bytes for an uncertain or resumable upload.
                    youtube.upload(self.cfg, self.store, job_id, self.cfg.output_dir / job_id)
                    return job_id
                if self.store.load(job_id, "script"):
                    self.store.update(job_id, job["state"], stage=3)
                    job = self.store.get(job_id)
            revision_path = self.cfg.output_dir / job_id / "script_revision.json"
            previous = self.store.load(job_id, "script")
            if job["stage"] == 3 and previous and revision_path.is_file():
                revision = ai.read_revision(revision_path.read_text(encoding="utf-8"))
                changed = revision.narration != [s["narration"] for s in previous["scenes"]]
                changed |= any(getattr(revision, k) != previous[k] for k in ("title", "description", "thumbnail_text"))
                if isinstance(revision, ai.EditorialDraft):
                    changed |= revision.headlines != [s["headline"] for s in previous["scenes"]]
                    changed |= revision.visual_queries != [s["visual_query"] for s in previous["scenes"]]
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

    def _generate_script(self, job_id, *args, **kwargs):
        topic = Topic.model_validate(self.store.load(job_id, "topic"))
        source = self.store.load(job_id, "source_brief")
        if source:
            source = youtube_content.SourceBrief.model_validate(source)
            if source.basis in {"youtube", "google_news"} and source.evidence is None:
                source = youtube_content.research_brief(self.cfg, self.web, self.llm, topic,
                    folder=self.cfg.output_dir / job_id)
                self._save(job_id, self.cfg.output_dir / job_id, "source_brief", source)
                self._save(job_id, self.cfg.output_dir / job_id, "research", source.evidence)
                self._save(job_id, self.cfg.output_dir / job_id, "editorial",
                           youtube_content.selection_details(topic, source))
            return youtube_content.generate(self.cfg, self.llm, topic,
                source, *args, **kwargs)
        if self.cfg.content_mode == "youtube_insights":
            raise ValueError("This saved run uses the previous verified-news workflow. "
                             "Start a new run to use creator topics with supporting article research")
        return ai.generate(self.cfg, self.llm, topic,
                           Research.model_validate(self.store.load(job_id, "research")), *args, **kwargs)

    def execute(self, job_id):
        cfg, db = self.cfg, self.store
        from .render_settings import RenderSelection
        # Jobs predating this feature retain their original Normal renderer.
        frozen = RenderSelection.model_validate({"motion_icons": False, "motion_lottie": False,
            "sound_effects": False, **(db.load(job_id, "render_settings") or {"render_mode": "normal"})})
        cfg = cfg.model_copy(update=frozen.model_dump())
        from .script_settings import freeze
        from .script_settings import snapshot as script_snapshot
        frozen_script = db.load(job_id, "script_settings")
        if frozen_script:
            cfg = freeze(cfg, frozen_script)
            if script_snapshot(self.cfg) != frozen_script:
                self.llm = ai.LLM(cfg)
            self.cfg = cfg
        job = db.get(job_id)
        if job["state"] in ("AWAITING_REVIEW", "PUBLISHED"):
            return job_id
        folder = cfg.output_dir / job_id
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if shutil.disk_usage(folder).free < cfg.min_free_gb * 1024**3:
                raise RuntimeError("CONFIGURATION: insufficient disk space")
            start = job["stage"]
            manual_input = (db.load(job_id, "request") or {}).get("manual_input")
            request = ManualRequest.model_validate(manual_input) if manual_input else None
            for index in range(start, len(STAGES)):
                db.update(job_id, STAGES[index], error=None)
                log.info("Job %s: %s", job_id, STAGES[index])
                if index == 0:
                    saved = db.load(job_id, "topic")
                    if saved:
                        candidates = [Topic.model_validate(saved)]
                    else:
                        candidates = (find_candidates(cfg, self.web, self.llm, request) if request else
                                      discovery.creator_and_news(cfg, self.web, refresh=True) if cfg.content_mode == "youtube_insights"
                                      and (db.load(job_id, "request") or {}).get("fresh") else
                                      discovery.discover(cfg, self.web))
                        previous = db.recent_topics(cfg.topic_dedup_days, cfg.youtube_privacy)
                        if not job["retries"]:
                            candidates = discovery.exclude_rejected(candidates, db.rejected_topic_history(job_id))
                        candidates = [t for t in candidates if not discovery.duplicate(t.title, previous)]
                        if not request and (cfg.prefer_tech_insights or cfg.topic_focus == "ai_coding"):
                            candidates = discovery.rank_insights(candidates, db.recent_topics(
                                cfg.topic_dedup_days, cfg.youtube_privacy), cfg.topic_focus, job.get("slot"))
                        if job["topic"] and not request:
                            candidates = [t for t in candidates if discovery.similarity(t.title, job["topic"]) >= .35]
                    if not candidates:
                        if cfg.content_mode == "youtube_insights":
                            db.update(job_id, "SKIPPED", error=(
                                "No recent, nonduplicate YouTube technology video matched your topic. "
                                "Add a YouTube video link or choose a different topic." if request else
                                "No unused technology topics from your selected creators or Google News RSS in the last seven days. "
                                "Published stories and previously skipped or failed topics are excluded. "
                                "Use your own script, try a different creator video link, or check again for fresh technology news."))
                            return job_id
                        if request:
                            db.update(job_id, "SKIPPED", error="No verified candidate for your chosen topic. "
                                      "Add official and independent source links or try again later. "
                                      "No alternative topic was selected.")
                            return job_id
                        db.update(job_id, "SKIPPED", error="No fresh, relevant, nonduplicate technology topic; "
                                  "Previously skipped or failed topics are excluded. Retry a saved run "
                                  "only if you deliberately want to recheck its topic.")
                        return job_id
                    if not saved:
                        self._save(job_id, folder, "candidates", [t.model_dump(mode="json") for t in candidates])
                    self._save(job_id, folder, "topic", candidates[0])
                    # A topic is reserved before research; failed stories do not repeatedly consume slots.
                    db.reserve_topic(job_id, candidates[0].title, discovery.fingerprint(candidates[0].title),
                                     cfg.youtube_privacy, cfg.topic_dedup_days)
                    db.update(job_id, STAGES[index], topic=candidates[0].title)
                elif index == 1:
                    research_started = datetime.now(timezone.utc).isoformat()
                    def research_progress(message):
                        db.save(job_id, "research_progress", {"message": message,
                            "started_at": research_started, "limit_seconds": research_duration(cfg)})
                        log.info("Research %s: %s", job_id, message)
                    with research_budget(research_duration(cfg), research_progress):
                        research_progress("Finding supporting sources")
                        if cfg.content_mode == "youtube_insights":
                            selected = Topic.model_validate(db.load(job_id, "topic"))
                            candidates = [Topic.model_validate(t) for t in (db.load(job_id, "candidates") or [])]
                            previous = [t for t in db.recent_topics(cfg.topic_dedup_days, cfg.youtube_privacy)
                                        if t != selected.title]
                            choices = [selected, *[t for t in candidates if t.url != selected.url]]
                            if not job["retries"]:
                                choices = discovery.exclude_rejected(choices, db.rejected_topic_history(job_id))
                            choices = [t for t in choices if not discovery.duplicate(t.title, previous)][:cfg.research_candidate_limit]
                            failures = []
                            for candidate in choices:
                                try:
                                    source = youtube_content.research_brief(cfg, self.web, self.llm, candidate, candidates, folder)
                                    if source.evidence and discovery.repeated_insight(
                                            [c.claim for c in source.evidence.claims],
                                            db.recent_research(cfg.topic_dedup_days, job_id, cfg.youtube_privacy)):
                                        raise ValueError("This supported story was already covered; trying another creator topic")
                                except ValueError as error:
                                    failures.append({"topic": candidate.title, "url": candidate.url, "reason": str(error)[:300]})
                                    self._save(job_id, folder, "research_attempts", failures)
                                    continue
                                self._save(job_id, folder, "topic", candidate)
                                db.reserve_topic(job_id, candidate.title, discovery.fingerprint(candidate.title),
                                                 cfg.youtube_privacy, cfg.topic_dedup_days)
                                db.update(job_id, STAGES[index], topic=candidate.title)
                                break
                            else:
                                db.update(job_id, "SKIPPED", error="Web research could not establish usable facts for the available creator and technology news topics. "
                                          "Official pages, documentation and credible web sources were checked; see research attempts.")
                                return job_id
                            self._save(job_id, folder, "source_brief", source)
                            if source.evidence:
                                self._save(job_id, folder, "research", source.evidence)
                            self._save(job_id, folder, "editorial", youtube_content.selection_details(candidate, source))
                        else:
                            candidates = [Topic.model_validate(t) for t in (db.load(job_id, "candidates") or [])
                                          if isinstance(t, dict)]
                            if job["retries"]:
                                fresh = (find_candidates(cfg, self.web, self.llm, request) if request else
                                         discovery.discover(cfg, self.web))
                                requested = (db.load(job_id, "request") or {}).get("topic_filter")
                                if requested and not request:
                                    fresh = [t for t in fresh if discovery.similarity(t.title, requested) >= .35]
                                # Refresh matching URLs as well as order; saved feed entries can be stale.
                                candidates = list({t.url: t for t in [*candidates, *fresh]}.values())
                                self._save(job_id, folder, "candidates", [t.model_dump(mode="json") for t in candidates])
                            selected = Topic.model_validate(db.load(job_id, "topic"))
                            selected = next((t for t in candidates if t.url == selected.url), selected)
                            choices = [selected, *[t for t in candidates if t.url != selected.url]]
                            if not job["retries"]:
                                choices = discovery.exclude_rejected(choices, db.rejected_topic_history(job_id))
                            previous = db.recent_topics(cfg.topic_dedup_days, cfg.youtube_privacy)
                            previous = [title for title in previous if title != selected.title]
                            if not request and (cfg.prefer_tech_insights or cfg.topic_focus == "ai_coding"):
                                choices = discovery.rank_insights(choices, previous, cfg.topic_focus, job.get("slot"))
                            # Ineligible entries must not consume the research budget.
                            choices = [t for t in choices if not discovery.duplicate(t.title, previous)
                                       and (t.content_kind == "evergreen" or
                                            0 <= (datetime.now(timezone.utc)-t.published_at).total_seconds()
                                            <= discovery.MAX_STORY_AGE_SECONDS)]
                            choices = ([t for t in choices if t.content_kind == "news"][:cfg.research_candidate_limit]
                                       + [t for t in choices if t.content_kind == "evergreen"])
                            prior_evidence = db.recent_research(cfg.topic_dedup_days, job_id, cfg.youtube_privacy)
                            failures = []
                            for candidate in choices:
                                self._save(job_id, folder, "topic", candidate)
                                db.update(job_id, STAGES[index], topic=candidate.title)
                                try:
                                    result = research.research(cfg, self.web, self.llm, candidate, candidates, folder)
                                    if discovery.repeated_insight([c.claim for c in result.claims], prior_evidence):
                                        raise ValueError("Verified insight already covered; choosing a different story")
                                except ValueError as error:
                                    retrieval_path = folder / "source_retrievals.json"
                                    retrievals = json.loads(retrieval_path.read_text(encoding="utf-8")) if retrieval_path.is_file() else []
                                    failures.append({"topic": candidate.title, "url": candidate.url, "reason": str(error)[:300],
                                                     "sources": retrievals})
                                    self._save(job_id, folder, "research_attempts", failures)
                                    log.info("Research candidate rejected: %s", str(error)[:150])
                                    continue
                                self._save(job_id, folder, "topic", candidate)
                                db.reserve_topic(job_id, candidate.title, discovery.fingerprint(candidate.title),
                                                 cfg.youtube_privacy, cfg.topic_dedup_days)
                                db.update(job_id, STAGES[index], topic=candidate.title)
                                break
                            else:
                                raise ValueError("No verified story available among " + str(len(failures))
                                                 + " checked topics. Open run details for source access and evidence failures; "
                                                 "retry later when fresh official coverage is available.")
                            self._save(job_id, folder, "research", result)
                            from .editorial import selection_details
                            self._save(job_id, folder, "editorial", selection_details(candidate, result, job.get("slot")))
                elif index == 2:
                    revision_path = folder / "script_revision.json"
                    revision = {}
                    if revision_path.is_file():
                        revision["reviewed_draft"] = ai.read_revision(revision_path.read_text(encoding="utf-8"))
                        log.info("Using local editorial revision; all evidence checks still apply")
                    if request and request.mode == "script":
                        revision["provided_narration"] = request.content
                    script = self._generate_script(job_id, **revision)
                    for old in db.recent_scripts(cfg.topic_dedup_days, job_id, cfg.youtube_privacy):
                        if discovery.duplicate(script.title, [old["title"]]) or \
                                discovery.similarity(script.script, old["script"]) > .92:
                            raise ValueError("Script/title too similar to a recent generated video")
                    self._save(job_id, folder, "script", script)
                elif index == 3:
                    self.llm.release()
                    script = Script.model_validate(db.load(job_id, "script"))
                    speech = audio.Speech(cfg)
                    attempts = []
                    for revision in range(narration_duration.MAX_REVISIONS + 1):
                        segments = speech.synthesize(script, folder)
                        duration = segments[-1]["end"]
                        attempts.append({"attempt": revision + 1, "seconds": duration,
                                         "words": len(script.script.split()),
                                         "within_limits": cfg.duration_min <= duration <= cfg.duration_max})
                        self._save(job_id, folder, "narration_duration_attempts", attempts)
                        if cfg.duration_min <= duration <= cfg.duration_max:
                            break
                        if request and request.mode == "script":
                            raise ValueError(f"Your script narration measured {duration:.1f}s, outside the "
                                             "duration limits. Shorten or revise your input and start a new run; "
                                             "your script was not rewritten.")
                        if (folder / "script_revision.json").is_file():
                            db.update(job_id, STAGES[index], stage=2)
                            raise ValueError(f"Reviewed narration measured {duration:.1f}s; revise "
                                             "script_revision.json to fit the configured duration and retry")
                        if revision == narration_duration.MAX_REVISIONS:
                            raise ValueError(f"Narration measured {duration:.1f}s after {revision} measured revisions; "
                                             f"required {cfg.duration_min:g}–{cfg.duration_max:g}s. "
                                             "Saved script and audio retained for retry; normal voice speed preserved.")
                        feedback = narration_duration.feedback(cfg, script, duration)
                        attempts[-1]["revision"] = {k: v for k, v in feedback.items() if k != "previous_draft"}
                        self._save(job_id, folder, "narration_duration_attempts", attempts)
                        log.info("Job %s: %s narration from %s words toward %s words for %.1f–%.1fs",
                                 job_id, feedback["direction"], feedback["measured_words"],
                                 feedback["target_words"], cfg.duration_min, cfg.duration_max)
                        script = self._generate_script(job_id, feedback)
                        narration_duration.validate(script, feedback)
                        self.llm.release()
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
                    report_path = folder / "render_report.json"
                    if report_path.is_file():
                        self._save(job_id, folder, "render_report", json.loads(report_path.read_text(encoding="utf-8")))
                    self._save(job_id, folder, "metadata", youtube.metadata(script, job_id,
                                                                            db.load(job_id, "media_assets"),
                                                                            cfg.youtube_privacy))
                elif index == 6:
                    render_report = db.load(job_id, "render_report") or {}
                    self._save(job_id, folder, "media_license_audit", licensing.audit(
                        [*db.load(job_id, "media_assets"), *render_report.get("creative_assets", [])]))
                    script = Script.model_validate(db.load(job_id, "script"))
                    if script.source_basis == "verified" and cfg.content_mode != "youtube_insights":
                        evidence = Research.model_validate(db.load(job_id, "research"))
                        research.verify_claims(evidence.claims, research.independent_sources(evidence.sources))
                        ai.validate_grounding(script, evidence)
                    result = video.quality(cfg, folder / "final.mp4", db.load(job_id, "caption_timing"),
                                           folder / "narration.wav")
                    self._save(job_id, folder, "validation", result)
                    topic = Topic.model_validate(db.load(job_id, "topic"))
                    if db.load(job_id, "source_brief"):
                        package = youtube_content.content_package(topic,
                            youtube_content.SourceBrief.model_validate(db.load(job_id, "source_brief")), script,
                            db.load(job_id, "caption_timing"), db.load(job_id, "media_assets"), folder)
                    else:
                        from .editorial import content_package
                        evidence = Research.model_validate(db.load(job_id, "research"))
                        package = content_package(topic, evidence, script, db.load(job_id, "caption_timing"),
                                                  db.load(job_id, "media_assets"), folder, job.get("slot"))
                    package["rendering"] = db.load(job_id, "render_report")
                    self._save(job_id, folder, "content_package", package)
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
        finally:
            try:
                self.llm.release()
            except RuntimeError:
                log.warning("Local model could not be unloaded; its bounded keep-alive will expire")
