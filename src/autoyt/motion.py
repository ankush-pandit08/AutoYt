"""Deterministic local scene rendering. No AI-generated code or remote render service."""
import hashlib
import json
import math
import shutil
from pathlib import Path

from .audio import probe, run
from .store import write_json

PROJECT = Path(__file__).with_name("remotion")


def video_background(cfg, asset, scene, folder):
    """Prepare only the selected licensed clip; source audio never enters narration."""
    if asset.get("kind") != "stock" or not asset.get("path"):
        return None
    from .licensing import check_asset
    from .store import digest
    check_asset(asset)
    source = Path(asset["path"])
    if source.stat().st_size > 80_000_000:
        raise ValueError("Motion background video exceeds the size limit")
    key = hashlib.sha256(json.dumps([digest(source), cfg.video_width, cfg.video_height,
        cfg.video_fps, scene["frames"]]).encode()).hexdigest()
    target = folder / ("background-"+key[:24]+".mp4")
    if not target.is_file():
        temporary = target.with_suffix(".tmp.mp4")
        run([cfg.ffmpeg, "-y", "-v", "error", "-stream_loop", "-1", "-i", source,
             "-vf", f"scale={cfg.video_width}:{cfg.video_height}:force_original_aspect_ratio=increase,"
                    f"crop={cfg.video_width}:{cfg.video_height},setsar=1,fps={cfg.video_fps}",
             "-frames:v", str(scene["frames"]), "-an", "-c:v", "libx264", "-preset", "fast",
             "-crf", "20", "-threads", "2", "-pix_fmt", "yuv420p", "-movflags", "+faststart", temporary])
        probe(cfg, temporary)
        temporary.replace(target)
    return {"kind": "video", "path": str(target.resolve()), "cache_key": key,
            "source_url": asset["source_url"]}


def image_background(cfg, asset):
    """Inline a bounded, licensed local photo so rendering never requests remote media."""
    if asset.get("kind") != "stock_image" or not asset.get("path"):
        return None
    import base64
    import io

    from PIL import Image, ImageOps

    from .licensing import check_asset
    check_asset(asset)
    path = Path(asset["path"])
    if path.stat().st_size > 12_000_000:
        raise ValueError("Motion background image exceeds the size limit")
    with Image.open(path) as image:
        if image.width*image.height > 40_000_000:
            raise ValueError("Motion background image exceeds the pixel limit")
        image = ImageOps.fit(ImageOps.exif_transpose(image).convert("RGB"),
                             (cfg.video_width, cfg.video_height))
        output = io.BytesIO()
        image.save(output, format="JPEG", quality=88)
    return {"kind": "image", "source_url": asset["source_url"],
            "src": "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode()}


def availability(cfg):
    node = shutil.which(cfg.remotion_node)
    packages = ["remotion", "@remotion/renderer", "@remotion/bundler"]
    if cfg.motion_icons:
        packages.extend(["@iconify/react", "@iconify-json/lucide"])
    if cfg.motion_lottie:
        packages.append("@remotion/lottie")
    installed = all((PROJECT / "node_modules" / package / "package.json").is_file()
                    for package in packages)
    browser = any(path.is_file() for path in (PROJECT/"node_modules/.remotion/chrome-headless-shell").glob(
        "*/*/chrome-headless-shell*"))
    if cfg.remotion_browser_executable:
        browser = bool(shutil.which(cfg.remotion_browser_executable) or Path(cfg.remotion_browser_executable).is_file())
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
    from . import creative
    if not availability(cfg)["ready"]:
        raise RuntimeError("CONFIGURATION: Install Node.js and run scripts/setup_motion.ps1 for local motion graphics")
    scenes = plan(cfg, script, segments, assets)
    clip_folder = folder / "motion"
    clip_folder.mkdir(exist_ok=True)
    library = creative.catalog(cfg) if cfg.motion_lottie else []
    credits = []
    for scene in scenes:
        if scene["renderer"] != "remotion":
            continue
        text = script.scenes[scene["index"]].visual_query + " " + scene["headline"]
        asset = assets[scene["index"]]
        scene["background"] = video_background(cfg, asset, scene, clip_folder) or image_background(cfg, asset)
        scene["icon"] = creative.topic_icon(text) if cfg.motion_icons else None
        if cfg.motion_lottie:
            scene["animation_data"], credit = creative.select_animation(cfg, text, library)
            credits.append(credit)
    if cfg.motion_icons:
        credits.append({"kind": "icon", "license": "ISC", "license_url": creative.LUCIDE_LICENSE,
                        "source_url": "https://lucide.dev", "attribution": "Lucide Contributors / Iconify (ISC)"})
    code = hashlib.sha256()
    for name in ("index.jsx", "render.mjs", "package-lock.json"):
        code.update((PROJECT/name).read_bytes())
    selected = []
    for scene in scenes:
        if scene["renderer"] != "remotion":
            continue
        fingerprint = hashlib.sha256(json.dumps({"scene": scene, "size": [cfg.video_width, cfg.video_height],
            "fps": cfg.video_fps, "crf": cfg.crf, "code": code.hexdigest()}, sort_keys=True).encode()).hexdigest()[:24]
        selected.append({**scene, "output": str((clip_folder / (fingerprint+".mp4")).resolve())})
    request = {"width": cfg.video_width, "height": cfg.video_height, "fps": cfg.video_fps,
               "crf": cfg.crf, "concurrency": cfg.remotion_concurrency, "scenes": selected,
               "browserExecutable": cfg.remotion_browser_executable or None,
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
    from .licensing import audit
    if credits:
        audit(credits)
    write_json(clip_folder / "creative-credits.json", credits)
    return output_assets, [{key: ({k: v for k, v in value.items() if k not in ("src", "path")} if key == "background" and value else value)
                            for key, value in scene.items() if key != "animation_data"} for scene in scenes]
