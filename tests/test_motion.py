from pathlib import Path

import pytest
from pydantic import ValidationError

from autoyt import motion, render_settings, video
from autoyt.dashboard import Dashboard
from autoyt.pipeline import Pipeline


def segments(script):
    return [{"text": s.narration, "start": i*.37, "end": (i+1)*.37} for i, s in enumerate(script.scenes)]


def test_mode_validation_and_saved_default(cfg):
    assert render_settings.record(cfg)["selection"]["render_mode"] == "normal"
    updated, saved = render_settings.save(cfg, {"render_mode": "hybrid", "render_fallback": False}, 0)
    assert saved["version"] == 1 and updated.render_mode == "hybrid"
    assert Pipeline(cfg).cfg.render_mode == "hybrid"  # Scheduled runs create the same Pipeline.
    with pytest.raises(ValueError, match="changed"):
        render_settings.save(cfg, saved["selection"], 0)
    with pytest.raises(ValidationError):
        render_settings.RenderSelection(render_mode="unknown")
    with pytest.raises(ValidationError):
        render_settings.RenderSelection(render_fallback="false")


def test_hybrid_retains_footage_and_covers_all_frames(cfg, script):
    cfg.render_mode = "hybrid"
    assets = [{"kind": "stock_image", "path": "licensed.jpg"} for _ in script.scenes]
    assets[2] = {"kind": "graphics", "path": None}
    planned = motion.plan(cfg, script, segments(script), assets)
    assert [s["renderer"] for s in planned] == ["remotion", "normal", "remotion", "normal", "normal", "remotion"]
    assert sum(s["frames"] for s in planned) == round(len(script.scenes)*.37*cfg.video_fps)
    assert [s["template"] for s in planned if s["renderer"] == "remotion"] == ["hook", "flow", "takeaway"]
    cfg.render_mode = "motion_graphics"
    assert all(s["renderer"] == "remotion" for s in motion.plan(cfg, script, segments(script), assets))
    assert [s["excerpt"] for s in planned] == [s.narration for s in script.scenes]


def test_bad_scene_timing_is_rejected(cfg, script):
    assets = [{"kind": "graphics", "path": None} for _ in script.scenes]
    with pytest.raises(ValueError, match="counts"):
        motion.plan(cfg, script, segments(script), assets[:-1])
    timing = segments(script)
    timing[1]["start"] += .1
    with pytest.raises(ValueError, match="continuous"):
        motion.plan(cfg, script, timing, assets)


def test_renderer_fallback_is_explicit_and_cannot_bypass_assembly(cfg, script, tmp_path, monkeypatch):
    cfg.render_mode = "hybrid"
    def fail(*args):
        raise RuntimeError("private provider message must never escape")
    monkeypatch.setattr(motion, "plan", lambda *args: [])
    monkeypatch.setattr(motion, "render_clips", fail)
    calls = []
    monkeypatch.setattr(video, "_render_normal", lambda *args: calls.append(args) or tmp_path/"final.mp4")
    video.render(cfg, script, [], [], tmp_path)
    import json
    report = json.loads((tmp_path/"render_report.json").read_text())
    assert report["requested_mode"] == "hybrid" and report["effective_mode"] == "normal"
    assert report["status"] == "completed" and report["fallback_reason"]
    assert "private provider" not in (tmp_path/"render_report.json").read_text()
    assert len(calls) == 1
    cfg.render_fallback = False
    with pytest.raises(RuntimeError, match="setup_motion"):
        video.render(cfg, script, [], [], tmp_path)
    assert len(calls) == 1


def test_job_selection_is_frozen_and_legacy_jobs_stay_normal(cfg, monkeypatch):
    runner = Pipeline(cfg)
    seen = []
    monkeypatch.setattr(runner, "execute", lambda ident: seen.append(ident) or ident)
    ident = runner.create_run(dry_run=True, render_mode="motion_graphics")
    assert runner.store.load(ident, "render_settings")["render_mode"] == "motion_graphics"
    runner.store.update(ident, "FAILED")
    render_settings.save(cfg, {"render_mode": "hybrid", "render_fallback": True}, 0)
    received = []
    # Resume at render stage to inspect effective configuration without API calls.
    runner.store.update(ident, "FAILED", stage=5)
    runner.store.save(ident, "media_assets", [])
    monkeypatch.setattr("autoyt.pipeline.licensing.audit", lambda *args: received.append(1) or {})
    monkeypatch.setattr("autoyt.pipeline.Script.model_validate", lambda *args: object())
    monkeypatch.setattr(video, "subtitles", lambda current, *args: (_ for _ in ()).throw(ValueError(current.render_mode)))
    new_runner = Pipeline(cfg)
    new_runner.execute(ident)
    assert "motion_graphics" in new_runner.store.get(ident)["error"]
    legacy = new_runner.store.create(dry_run=True)
    new_runner.store.update(legacy, "FAILED", stage=5)
    new_runner.execute(legacy)
    assert "normal" in new_runner.store.get(legacy)["error"]


def test_dashboard_saves_defaults_and_forwards_manual_mode(cfg, monkeypatch):
    app = Dashboard(cfg)
    try:
        result = app.action("save_render", manual_input={"version": 0, "selection": {"render_mode": "hybrid", "render_fallback": True}})
        assert result["render_settings"]["selection"]["render_mode"] == "hybrid"
        assert app.snapshot()["render_settings"]["version"] == 1
        calls = []
        monkeypatch.setattr(app, "_run", lambda *args: calls.append(args))
        app.action("preview", render_mode="motion_graphics")
        app.future.result(timeout=5)
        assert calls[0][-1] == "motion_graphics"
        with pytest.raises(ValueError, match="retries"):
            app.action("retry", render_mode="hybrid")
    finally:
        app.close()


def test_normal_does_not_need_node(cfg, script, tmp_path, monkeypatch):
    monkeypatch.setattr(motion, "render_clips", lambda *args: pytest.fail("Normal must not invoke Node"))
    monkeypatch.setattr(video, "_render_normal", lambda *args: Path("normal.mp4"))
    assert video.render(cfg, script, [], [], tmp_path) == Path("normal.mp4")


def test_full_motion_does_not_download_unused_stock(cfg, script):
    from autoyt.media import choose_media
    cfg.render_mode = "motion_graphics"
    cfg.pexels_api_key, cfg.pixabay_api_key, cfg.enable_public_images = "test", "test", True
    class Offline:
        def get(self, *args, **kwargs):
            pytest.fail("Full motion should not request stock footage")
    assets = choose_media(cfg, Offline(), script)
    from autoyt.licensing import audit
    assert len(assets) == len(script.scenes) and audit(assets)["passed"]
    assert all(a["path"] is None for a in assets)


def test_script_provider_selection_is_separate_from_voice_and_hides_keys(cfg):
    from autoyt import script_settings
    cfg.groq_api_key, cfg.gemini_api_key = "test-groq-secret", "test-gemini-secret"
    app = Dashboard(cfg)
    try:
        voice = app.cfg.tts_voice
        app.action("save_script", manual_input={"version": 0, "selection": {"provider": "groq"}})
        assert app.cfg.llm_provider == "groq" and app.cfg.tts_voice == voice
        app.action("save_script", manual_input={"version": 1, "selection": {"provider": "gemini"}})
        assert Pipeline(cfg).cfg.llm_provider == "gemini"
        import json
        snapshot = json.dumps(app.snapshot())
        assert "test-groq-secret" not in snapshot and "test-gemini-secret" not in snapshot
        assert set(app.snapshot()["script_providers"]) == {"groq", "gemini", "openrouter"}
        with pytest.raises(ValueError, match="changed"):
            script_settings.save(cfg, {"provider": "groq"}, 0)
        cfg.groq_api_key = ""
        with pytest.raises(ValueError, match="GROQ_API_KEY"):
            script_settings.save(cfg, {"provider": "groq"}, 2)
        with pytest.raises(ValidationError):
            script_settings.ScriptSelection(provider="unknown")
    finally:
        app.close()


def test_retry_keeps_script_provider_and_model(cfg, monkeypatch):
    from autoyt import script_settings
    cfg.llm_provider, cfg.groq_model = "groq", "saved-groq-model"
    cfg.groq_api_key, cfg.gemini_api_key = "test-groq-secret", "test-gemini-secret"
    runner = Pipeline(cfg)
    monkeypatch.setattr(runner, "execute", lambda ident: ident)
    ident = runner.create_run(dry_run=True)
    script_settings.save(cfg, {"provider": "gemini"}, 0)
    runner.store.update(ident, "FAILED", stage=5)
    runner.store.save(ident, "media_assets", [])
    monkeypatch.setattr("autoyt.pipeline.licensing.audit", lambda *args: {})
    monkeypatch.setattr("autoyt.pipeline.Script.model_validate", lambda *args: object())
    monkeypatch.setattr(video, "subtitles", lambda current, *args: (_ for _ in ()).throw(
        ValueError(current.llm_provider+":"+current.groq_model)))
    resumed = Pipeline(cfg)
    assert resumed.cfg.llm_provider == "gemini"
    resumed.execute(ident)
    assert "groq:saved-groq-model" in runner.store.get(ident)["error"]
