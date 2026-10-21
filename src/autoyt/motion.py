"""Deterministic local scene rendering. No AI-generated code or remote render service."""
import hashlib
import json
import math
import shutil
from pathlib import Path

from .audio import probe, run
from .store import write_json

PROJECT = Path(__file__).with_name("remotion")


def availability(cfg):
    node = shutil.which(cfg.remotion_node)
    installed = all((PROJECT / "node_modules" / package / "package.json").is_file()
                    for package in ("remotion", "@remotion/renderer", "@remotion/bundler"))
    browser = any(path.is_file() for path in (PROJECT/"node_modules/.remotion/chrome-headless-shell").glob(
        "*/*/chrome-headless-shell*"))
    return {"ready": bool(node and installed and browser), "node": bool(node), "packages": installed, "browser": browser}


def plan(cfg, script, segments, assets):
    if not (len(script.scenes) == len(segments) == len(assets)) or not segments:
        raise ValueError("Scene, narration and visual counts must match")
    result = []
    previous = 0
    for index, (scene, segment, asset) in enumerate(zip(script.scenes, segments, assets)):
        if not all(math.isfinite(segment[k]) for k in ("start", "end")):
            raise ValueError("Scene timing must be finite")
        start, end = round(segment["start"]*cfg.video_fps), round(segment["end"]*cfg.video_fps)
        if start != previous or end <= start:
            raise ValueError("Scene timing must be continuous and cover at least one frame")
        previous = end
        motion = cfg.render_mode == "motion_graphics" or (cfg.render_mode == "hybrid" and (
            index in (0, len(segments)-1) or asset["kind"] == "graphics" or not asset.get("path")))
        template = "hook" if index == 0 else "takeaway" if index == len(segments)-1 else "flow" if index % 2 == 0 else "focus"
        # Text is copied from the saved script; visuals add no specifications or measurements.
        result.append({"index": index, "total": len(segments), "frames": end-start,
                       "start_frame": start, "end_frame": end, "renderer": "remotion" if motion else "normal",
                       "template": template if motion else None, "headline": scene.headline,
                       "excerpt": scene.narration})
    return result


def render_clips(cfg, script, segments, assets, folder):
    if not availability(cfg)["ready"]:
        raise RuntimeError("CONFIGURATION: Install Node.js and run scripts/setup_motion.ps1 for local motion graphics")
    scenes = plan(cfg, script, segments, assets)
    code = hashlib.sha256()
    for name in ("index.jsx", "render.mjs", "package-lock.json"):
        code.update((PROJECT/name).read_bytes())
    clip_folder = folder / "motion"
    clip_folder.mkdir(exist_ok=True)
    selected = []
    for scene in scenes:
        if scene["renderer"] != "remotion":
            continue
        fingerprint = hashlib.sha256(json.dumps({"scene": scene, "size": [cfg.video_width, cfg.video_height],
            "fps": cfg.video_fps, "crf": cfg.crf, "code": code.hexdigest()}, sort_keys=True).encode()).hexdigest()[:24]
        selected.append({**scene, "output": str((clip_folder / (fingerprint+".mp4")).resolve())})
    request = {"width": cfg.video_width, "height": cfg.video_height, "fps": cfg.video_fps,
               "crf": cfg.crf, "concurrency": cfg.remotion_concurrency, "scenes": selected,
               "bundleCache": str((cfg.cache_dir / "remotion-bundles").resolve()),
               "receipt": str((clip_folder / "receipt.json").resolve()), "stills": True}
    write_json(clip_folder / "request.json", request)
    try:
        run([cfg.remotion_node, PROJECT/"render.mjs", (clip_folder/"request.json").resolve()],
            cwd=PROJECT, timeout=cfg.remotion_timeout_seconds)
    except Exception:
        # Do not expose arbitrary browser/transport messages; they can contain source data.
        raise RuntimeError("TRANSIENT: Local Remotion rendering failed; check Node/browser setup or use Normal mode") from None
    receipt = json.loads((clip_folder / "receipt.json").read_text(encoding="utf-8"))
    if receipt.get("completed") != [s["output"] for s in selected]:
        raise RuntimeError("VALIDATION: Motion renderer did not complete every requested scene")
    output_assets = [dict(asset) for asset in assets]
    for scene in selected:
        info = probe(cfg, scene["output"])
        stream = next((s for s in info["streams"] if s.get("codec_type") == "video"), {})
        rate = stream.get("avg_frame_rate", "0/1").split("/")
        if (stream.get("width"), stream.get("height")) != (cfg.video_width, cfg.video_height) or \
                stream.get("codec_name") != "h264" or abs(float(info["format"]["duration"])-scene["frames"]/cfg.video_fps) > .1 or \
                abs(int(rate[0])/max(1, int(rate[1]))-cfg.video_fps) > .1:
            raise RuntimeError("VALIDATION: Motion scene has incorrect dimensions, codec, frame rate or duration")
        output_assets[scene["index"]] = {"kind": "motion", "path": scene["output"]}
    return output_assets, scenes
