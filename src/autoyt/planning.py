"""One persistent morning script plan; scheduled slots consume it without rediscovery."""
import json
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from . import ai, discovery, research, youtube_content
from .net import Web
from .pipeline import pipeline_lock
from .store import Store, write_json

log = logging.getLogger(__name__)


def plan_path(cfg, moment=None):
    local = (moment or datetime.now(ZoneInfo(cfg.timezone))).astimezone(ZoneInfo(cfg.timezone))
    return cfg.database_path.parent / "daily-plans" / (local.strftime("%Y-%m-%d") + ".json")


def load_plan(cfg, moment=None):
    path = plan_path(cfg, moment)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def prepare_day(cfg, moment=None):
    from .script_settings import apply as apply_script
    cfg = apply_script(cfg)
    from .voice_settings import apply
    cfg = apply(cfg)
    cfg.prepare()
    with pipeline_lock(cfg):
        path = plan_path(cfg, moment)
        path.parent.mkdir(parents=True, exist_ok=True)
        plan = load_plan(cfg, moment)
        if plan and plan["state"] in ("READY", "PARTIAL", "FAILED"):
            return plan
        store, web, llm = Store(cfg.database_path), Web(), ai.LLM(cfg)
        if not plan:
            # Persist discovery once. An interrupted preparation resumes this same snapshot.
            candidates = discovery.discover(cfg, web)
            previous = store.recent_topics(cfg.topic_dedup_days, cfg.youtube_privacy)
            if cfg.content_mode != "youtube_insights":
                previous += store.recent_research_rejections()
            if cfg.prefer_tech_insights or cfg.topic_focus == "ai_coding":
                candidates = discovery.rank_insights(candidates, store.recent_topics(
                    cfg.topic_dedup_days, cfg.youtube_privacy), cfg.topic_focus)
            selected = []
            for topic in candidates:
                if discovery.duplicate(topic.title, previous + [t.title for t in selected]):
                    continue
                selected.append(topic)
            plan = {"date": path.stem, "state": "PREPARING", "content_mode": cfg.content_mode,
                "prepared_at": datetime.now(
                ZoneInfo(cfg.timezone)).isoformat(), "entries": [], "rejected": [],
                "candidates": [t.model_dump(mode="json") for t in (
                    [t for t in selected if t.content_kind == "news"][:cfg.research_candidate_limit]
                    + [t for t in selected if t.content_kind == "evergreen"])], "cursor": 0,
                "slots": cfg.schedule_times.split(",")[:cfg.max_videos_per_day]}
            write_json(path, plan)
        from .models import Topic
        candidates = [Topic.model_validate(t) for t in plan["candidates"]]
        prior_evidence = store.recent_research(cfg.topic_dedup_days, visibility=cfg.youtube_privacy)
        try:
            while plan["cursor"] < len(candidates) and len(plan["entries"]) < len(plan["slots"]):
                topic = candidates[plan["cursor"]]
                folder = path.parent / (path.stem + "-research") / str(plan["cursor"])
                folder.mkdir(parents=True, exist_ok=True)
                try:
                    if cfg.content_mode == "youtube_insights":
                        source = youtube_content.research_brief(cfg, web, llm, topic, candidates, folder)
                        planned = [e["source_brief"]["evidence"] for e in plan["entries"]
                                   if e.get("source_brief", {}).get("evidence")]
                        if source.evidence and discovery.repeated_insight(
                                [c.claim for c in source.evidence.claims], prior_evidence + planned):
                            raise ValueError("This supported story was already covered or planned")
                        script = youtube_content.generate(cfg, llm, topic, source)
                        source_artifact = {"source_brief": source.model_dump(mode="json")}
                    else:
                        evidence = research.research(cfg, web, llm, topic, candidates, folder)
                        if discovery.repeated_insight([c.claim for c in evidence.claims],
                                                     prior_evidence + [e["research"] for e in plan["entries"]]):
                            raise ValueError("Verified insight already covered by another video")
                        script = ai.generate(cfg, llm, topic, evidence)
                        source_artifact = {"research": evidence.model_dump(mode="json")}
                    prior = [e["script"]["title"] for e in plan["entries"]]
                    prior += [s["title"] for s in store.recent_scripts(cfg.topic_dedup_days, "", cfg.youtube_privacy)]
                    if discovery.duplicate(script.title, prior):
                        raise ValueError("Script duplicates another planned or published story")
                    plan["entries"].append({"slot": plan["slots"][len(plan["entries"])],
                        "topic": topic.model_dump(mode="json"), **source_artifact,
                        "script": script.model_dump(mode="json")})
                    log.info("Morning plan: %s/%s scripts ready", len(plan["entries"]), len(plan["slots"]))
                except ValueError:
                    plan["rejected"].append({"topic": topic.title, "reason":
                        "Supporting article research, script or duplicate check failed" if cfg.content_mode == "youtube_insights"
                        else "Evidence, script or originality check failed"})
                plan["cursor"] += 1
                write_json(path, plan)
            plan["state"] = "READY" if len(plan["entries"]) == len(plan["slots"]) else "PARTIAL"
        except RuntimeError:
            plan["state"] = "PARTIAL" if plan["entries"] else "FAILED"
            plan["error"] = "API or runtime unavailable; check logs and provider quotas. No replacement discovery today."
        finally:
            write_json(path, plan)
            llm.release()
        return plan
