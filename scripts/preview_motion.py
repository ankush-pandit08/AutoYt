"""Offline renderer comparison using an existing English voice sample; never uploads."""
import argparse
import json
import shutil
from pathlib import Path

from autoyt import licensing, video
from autoyt.audio import check_audio
from autoyt.config import Settings
from autoyt.models import Scene, Script
from autoyt.store import write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["normal", "motion_graphics", "hybrid", "all"], default="all")
    parser.add_argument("--width", type=int, default=540)
    parser.add_argument("--audio", type=Path, default=Path("output/voice-previews/kokoro-michael-english.wav"))
    parser.add_argument("--assets-json", type=Path, help="Optional existing licensed image/footage records for Hybrid")
    args = parser.parse_args()
    if args.width % 18 or args.width < 180:
        parser.error("Width must be a multiple of 18 and at least 180 (e.g. 540 or 1080)")
    if not args.audio.is_file():
        parser.error("Supply an existing English audio file with --audio; this demo does not call a voice API")
    cfg = Settings().model_copy(update={"video_width": args.width, "video_height": args.width*16//9,
        "duration_min": .5, "duration_max": 120, "render_fallback": False,
        "narration_language": "en", "script_style": "standard"})
    texts = ["Here is your daily technology insight.", "We explain artificial intelligence,",
             "coding tools, and useful updates", "in clear, simple English."]
    headlines = ["Technology, brought to life", "Ideas you can see", "Make the connection", "Clear. Useful. Understandable."]
    script = Script(natural_pacing=True, source_basis="user_input", topic="Local motion graphics demonstration",
        title="Technology explained through motion", hook=texts[0], script=" ".join(texts),
        description="Offline renderer demonstration. Narration and caption phrase timing are illustrative.",
        hashtags=["#Technology", "#Shorts"], visual_keywords=["computer technology"], thumbnail_text="TECH IN MOTION",
        scenes=[Scene(narration=text, headline=headline, visual_query="computer technology")
                for text, headline in zip(texts, headlines)])
    duration = check_audio(args.audio)["duration"]
    cursor, segments = 0.0, []
    words = sum(len(t.split()) for t in texts)
    for text in texts:
        end = cursor + duration*len(text.split())/words
        segments.append({"start": cursor, "end": end, "text": text})
        cursor = end
    assets = [{"kind": "graphics", "path": None, "license": "Original AutoYT graphics", "attribution": "AutoYT"}
              for _ in texts]
    if args.assets_json:
        asset = json.loads(args.assets_json.read_text(encoding="utf-8"))[0]
        if not Path(asset["path"]).is_file():
            parser.error("The supplied licensed media file does not exist")
        assets[1] = asset
    licensing.audit(assets)
    modes = ["normal", "motion_graphics", "hybrid"] if args.mode == "all" else [args.mode]
    for mode in modes:
        cfg = cfg.model_copy(update={"render_mode": mode})
        folder = Path("output/motion-previews") / mode
        folder.mkdir(parents=True, exist_ok=True)
        shutil.copy2(args.audio, folder/"narration.wav")
        write_json(folder/"script.json", script.model_dump(mode="json"))
        write_json(folder/"media_assets.json", assets)
        captions = video.subtitles(cfg, segments, folder)
        print("Rendering", mode, flush=True)
        result = video.render(cfg, script, segments, assets, folder)
        report = video.quality(cfg, result, captions, folder/"narration.wav")
        write_json(folder/"validation.json", report)
        print(mode, "passed:", result.resolve(), "duration", report["duration"], flush=True)


if __name__ == "__main__":
    main()
