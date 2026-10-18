"""Real local media smoke test; deterministic educational fixture, never a live-news/upload test."""
import json
from pathlib import Path

from autoyt.audio import Speech, check_audio, run
from autoyt.config import Settings
from autoyt.models import Script
from autoyt.store import write_json
from autoyt.video import quality, render, subtitles
from autoyt.youtube import metadata


def main():
    cfg = Settings()
    folder = (cfg.output_dir/"local-smoke").resolve()
    folder.mkdir(parents=True, exist_ok=True)
    fixture = Path(__file__).resolve().parents[1]/"tests/fixtures/script.json"
    script = Script.model_validate_json(fixture.read_text(encoding="utf-8"))
    segments = Speech(cfg).synthesize(script, folder)
    duration = segments[-1]["end"]
    # This test's educational text is fixed. Fit it to 45s without using an LLM.
    # Production instead regenerates the script when narration fails duration tolerance.
    speed = duration/cfg.video_duration_seconds
    if not .7 <= speed <= 1.3:
        raise ValueError(f"Test voice pace {speed:.2f} too far from natural; choose another voice")
    run([cfg.ffmpeg, "-y", "-v", "error", "-i", folder/"narration.wav", "-af", f"atempo={speed}",
         folder/"fitted.wav"])
    (folder/"fitted.wav").replace(folder/"narration.wav")
    actual = check_audio(folder/"narration.wav")["duration"]
    for segment in segments:
        segment["start"] *= actual/duration
        segment["end"] *= actual/duration
    assets = [{"kind": "graphics", "path": None, "license": "Original graphics",
               "attribution": "Original AutoYT graphics; local educational render test", "source_url": None}
              for _ in script.scenes]
    write_json(folder/"script.json", script.model_dump(mode="json"))
    write_json(folder/"topic.json", {"title": script.topic, "test_fixture": True})
    write_json(folder/"research.json", {"test_fixture": True, "live_news_verified": False,
                                       "note": "Fixed educational renderer fixture; no live research/model used"})
    write_json(folder/"timing.json", segments)
    write_json(folder/"media_assets.json", assets)
    timings = subtitles(cfg, segments, folder)
    render(cfg, script, segments, assets, folder)
    write_json(folder/"metadata.json", metadata(script, "local-smoke", assets))
    report = quality(cfg, folder/"final.mp4", timings, folder/"narration.wav")
    report["test_fixture"] = True
    report["voice_engine"] = cfg.tts_engine
    write_json(folder/"validation.json", report)
    print(json.dumps(report, indent=2))
    print(folder/"final.mp4")


if __name__ == "__main__":
    main()
