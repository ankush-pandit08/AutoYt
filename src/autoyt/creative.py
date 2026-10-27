"""Offline, licensed decorative assets. No scraping or remote assets in the renderer."""
import base64
import hashlib
import json
import math
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from .store import write_json

BUILTIN = Path(__file__).with_name("creative_assets")
CC0 = "https://creativecommons.org/publicdomain/zero/1.0/"
LOTTIE_LICENSE = "https://lottiefiles.com/page/license"
LUCIDE_LICENSE = "https://lucide.dev/license"
FEATURES = ("motion_icons", "motion_lottie", "sound_effects")


def topic_icon(text):
    import re
    tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
    for terms, icon, label in (
        ({"security", "privacy", "password", "passkeys", "hack", "cybersecurity"}, "shield-check", "SECURITY"),
        ({"code", "coding", "developer", "programming", "github", "software", "api"}, "code-xml", "CODE & TOOLS"),
        ({"chip", "chips", "gpu", "nvidia", "processor", "hardware"}, "cpu", "COMPUTING"),
        ({"ai", "llm", "llms", "model", "models", "neural", "gpt", "claude", "gemini"}, "brain-circuit", "AI & MODELS"),
        ({"cloud", "server", "data", "database"}, "database", "DATA & CLOUD"),
        ({"phone", "android", "ios", "iphone", "mobile"}, "smartphone", "MOBILE TECH"),
    ):
        if tokens & terms:
            return {"name": icon, "label": label}
    return {"name": "network", "label": "TECH INSIGHT"}


def validate_lottie(data):
    """Accept bounded vector Lottie JSON; reject expressions, images, fonts and network references."""
    if not isinstance(data, dict):
        raise ValueError("Choose a vector Lottie JSON file")
    try:
        fps, start, end, w, h = (float(data[k]) for k in ("fr", "ip", "op", "w", "h"))
        if not all(math.isfinite(v) for v in (fps, start, end, w, h)) or not (
                1 <= fps <= 60 and 0 <= start < end and end-start <= 600 and
                (end-start)/fps <= 20 and 1 <= w <= 2048 and 1 <= h <= 2048):
            raise ValueError()
        if not isinstance(data.get("layers"), list) or not 1 <= len(data["layers"]) <= 80:
            raise ValueError()
        count = 0

        def walk(value, depth=0):
            nonlocal count
            count += 1
            if count > 50000 or depth > 40:
                raise ValueError()
            if isinstance(value, dict):
                if any(key in value for key in ("fonts", "chars", "ef", "p", "u")):
                    # 'p' is also the normal position property; only strings are external asset paths.
                    if any(key in value for key in ("fonts", "chars")) or value.get("ef") or any(
                            isinstance(value.get(key), str) for key in ("p", "u")):
                        raise ValueError()
                if isinstance(value.get("x"), str):
                    raise ValueError()  # After Effects expression
                if isinstance(value.get("ty"), int) and value["ty"] not in (0, 1, 3, 4):
                    raise ValueError()
                if value.get("ty") == "rp":
                    raise ValueError()  # Unbounded repeaters can allocate enormous SVG trees.
                for child in value.values():
                    walk(child, depth+1)
            elif isinstance(value, list):
                for child in value:
                    walk(child, depth+1)
            elif isinstance(value, float) and not math.isfinite(value):
                raise ValueError()
        walk(data)
    except (KeyError, TypeError, ValueError, RecursionError, OverflowError):
        raise ValueError("Use a vector-only Lottie JSON: up to 20 seconds, 600 frames, 2048px, without expressions, images, text, effects or external files") from None
    return data


def catalog(cfg, verify_files=True):
    records = []
    path = cfg.creative_manifest
    if path.is_file():
        try:
            entries = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(entries, list) or len(entries) > 50:
                return []
            for entry in entries:
                file = (path.parent / entry["file"]).resolve()
                if not file.is_relative_to(path.parent.resolve()) or not file.is_file() or file.is_symlink():
                    continue
                if file.stat().st_size > 8_000_000:
                    continue
                from .licensing import check_asset
                check_asset(entry)
                if verify_files:
                    raw = file.read_bytes()
                    if hashlib.sha256(raw).hexdigest() != entry.get("sha256"):
                        continue
                    if entry["kind"] == "animation":
                        validate_lottie(json.loads(raw))
                records.append({**entry, "path": str(file)})
        except (KeyError, ValueError, TypeError, OSError):
            return []
    return records


def library_status(cfg):
    items = catalog(cfg, verify_files=False)
    return {"animations": sum(a["kind"] == "animation" for a in items),
            "sounds": sum(a["kind"] == "sound_effect" for a in items),
            "items": [{"name": a["name"], "kind": a["kind"], "source_url": a["source_url"],
                       "license": a["license"], "attribution": a["attribution"]} for a in items]}


def import_asset(cfg, value):
    """Called only through the loopback dashboard's CSRF-protected, idle-only import endpoint."""
    import soundfile as sf

    from .audio import run

    if not isinstance(value, dict) or value.get("kind") not in ("animation", "sound_effect"):
        raise ValueError("Choose animation or sound effect")
    kind = value["kind"]
    source = str(value.get("source_url", ""))
    parsed = urlsplit(source)
    allowed = {"lottiefiles.com", "www.lottiefiles.com"} if kind == "animation" else {"freesound.org", "www.freesound.org"}
    if parsed.scheme != "https" or parsed.hostname not in allowed or parsed.username or parsed.password or parsed.query or parsed.fragment or len(source) > 500:
        raise ValueError("Enter the original HTTPS LottieFiles or Freesound asset page, without query parameters")
    license_name = "Lottie Simple License" if kind == "animation" else "CC0-1.0"
    if value.get("license") != license_name or value.get("rights_confirmed") is not True:
        raise ValueError("Confirm the asset page grants the selected license before importing")
    name, author = str(value.get("name", "")).strip(), str(value.get("author", "")).strip()
    tags = value.get("tags", [])
    if not (1 <= len(name) <= 80 and 1 <= len(author) <= 100 and isinstance(tags, list)
            and 1 <= len(tags) <= 12 and all(isinstance(t, str) and 1 <= len(t) <= 30 for t in tags)):
        raise ValueError("Add a name, creator, and 1–12 short topic tags")
    try:
        raw = base64.b64decode(value.get("data", ""), validate=True)
    except (ValueError, TypeError):
        raise ValueError("File data is invalid") from None
    if not raw or len(raw) > (2_000_000 if kind == "animation" else 8_000_000):
        raise ValueError("Animation limit is 2 MB; sound limit is 8 MB")
    root = cfg.creative_manifest.parent
    root.mkdir(parents=True, exist_ok=True)
    existing = json.loads(cfg.creative_manifest.read_text(encoding="utf-8")) if cfg.creative_manifest.is_file() else []
    if not isinstance(existing, list) or len(existing) >= 50:
        raise ValueError("Creative library supports up to 50 imported assets")
    with tempfile.TemporaryDirectory(prefix="creative-", dir=root) as temporary:
        temp = Path(temporary)
        if kind == "animation":
            try:
                data = validate_lottie(json.loads(raw))
            except (UnicodeError, ValueError, RecursionError):
                raise ValueError("Choose a supported vector-only Lottie JSON file") from None
            raw = json.dumps(data, allow_nan=False, separators=(",", ":")).encode()
        else:
            input_file = temp / "input.audio"
            input_file.write_bytes(raw)
            output = temp / "effect.wav"
            try:
                run([cfg.ffmpeg, "-y", "-v", "error", "-protocol_whitelist", "file,pipe",
                     "-format_whitelist", "wav,mp3,ogg", "-i", input_file,
                     "-t", "5.1", "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", output], timeout=25)
                samples, rate = sf.read(output)
                import numpy as np
                if not .03 <= len(samples)/rate <= 5 or not np.isfinite(samples).all() or max(abs(samples)) < .001:
                    raise ValueError()
                raw = output.read_bytes()
            except Exception:
                raise ValueError("Choose a valid audible WAV/MP3/OGG sound effect no longer than 5 seconds") from None
    digest = hashlib.sha256(raw).hexdigest()
    filename = digest + (".json" if kind == "animation" else ".wav")
    record = {"kind": kind, "file": filename, "sha256": digest, "name": name, "tags": tags,
              "source_url": source, "license": license_name,
              "license_url": LOTTIE_LICENSE if kind == "animation" else CC0,
              "attribution": f"{name} by {author} · {source}", "free_to_use": True, "permitted_use": True}
    if not any(a.get("sha256") == digest for a in existing):
        (root / filename).write_bytes(raw)
        write_json(cfg.creative_manifest, [*existing, record])
    return {"message": "Asset imported. Matching future scenes can use it.", "creative_library": library_status(cfg)}


def select_animation(cfg, text, items):
    from .discovery import words
    candidates = [(len(words(text) & words(" ".join(a.get("tags", [])))), a)
                  for a in items if a["kind"] == "animation"]
    score, selected = max(candidates, key=lambda pair: pair[0], default=(0, None))
    if score:
        return validate_lottie(json.loads(Path(selected["path"]).read_text(encoding="utf-8"))), selected
    return json.loads((BUILTIN / "tech-pulse.json").read_text()), {
        "kind": "animation", "license": "Original AutoYT graphics", "attribution": "AutoYT original vector pulse",
        "source_url": None}


def sound_bus(cfg, segments, folder, items):
    """Bounded accent bus, max six cues with headroom. Final ducking happens in FFmpeg."""
    import numpy as np
    import soundfile as sf

    from .discovery import words

    if not cfg.sound_effects or not segments:
        return None, []
    duration = float(segments[-1]["end"])
    if not math.isfinite(duration) or not 0 < duration <= 120:
        raise ValueError("Sound effects require a bounded video duration")
    rate = 48000
    bus = np.zeros(math.ceil(duration*rate), dtype=np.float32)
    records, previous = [], -10.0
    for segment in segments[:6]:
        start = float(segment["start"])
        if not math.isfinite(start) or not 0 <= start < duration:
            raise ValueError("Invalid sound cue timing")
        if start-previous < 1.5:
            continue
        previous = start
        candidates = [(len(words(segment["text"]) & words(" ".join(a.get("tags", [])))), a)
                      for a in items if a["kind"] == "sound_effect"]
        score, selected = max(candidates, key=lambda p: p[0], default=(0, None))
        sound = None
        if score:
            try:
                samples, sample_rate = sf.read(selected["path"], dtype="float32")
                if sample_rate == rate and samples.ndim == 1 and 0 < len(samples) <= rate*5 and np.isfinite(samples).all():
                    sound = samples[:int(rate*.8)]
            except (OSError, ValueError):
                pass
        if sound is None:
            time = np.arange(int(rate*.22))/rate
            sound = np.sin(2*np.pi*(620*time + 850*time*time))*np.exp(-time*23)
            credit = {"kind": "sound_effect", "license": "Original AutoYT audio",
                      "attribution": "AutoYT original synthesized transition", "source_url": None}
        else:
            credit = selected
        peak = float(np.max(np.abs(sound)))
        if peak <= .00001:
            continue
        sound = sound/peak*.10
        fade = min(len(sound)//2, 960)
        sound[:fade] *= np.linspace(0, 1, fade)
        sound[-fade:] *= np.linspace(1, 0, fade)
        offset = round(start*rate)
        count = min(len(sound), len(bus)-offset)
        bus[offset:offset+count] += sound[:count]
        records.append({**credit, "start": start, "duration": count/rate})
    path = folder / "effects.wav"
    sf.write(path, np.clip(bus, -.18, .18), rate, subtype="PCM_16")
    return path, records
