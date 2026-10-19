import math
import re
import shutil
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .audio import check_audio, probe, run


def font(size, bold=False):
    candidates = [Path("data/fonts/NotoSansDevanagari-Bold.ttf" if bold else "data/fonts/NotoSansDevanagari.ttf"),
                  Path("C:/Windows/Fonts/NirmalaB.ttf" if bold else "C:/Windows/Fonts/Nirmala.ttf"),
                  Path("/usr/share/fonts/truetype/noto/NotoSansDevanagari-Bold.ttf" if bold else
                       "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf"),
                  Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
                  Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else
                       "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
    # A regular variable font is not a bold face; prefer installed real bold fonts.
    if bold:
        candidates.append(Path("data/fonts/NotoSansDevanagari.ttf"))
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def local_fonts(cfg, folder):
    source = cfg.cache_dir.parent / "fonts"
    target = folder / ".fonts"
    if cfg.narration_language == "hi" and not (source / "NotoSansDevanagari.ttf").is_file():
        raise RuntimeError("CONFIGURATION: Hindi font missing; run python scripts/setup_kokoro.py")
    if source.is_dir():
        target.mkdir(exist_ok=True)
        for name in ("NotoSansDevanagari.ttf", "OFL.txt"):
            if (source / name).is_file() and not (target / name).is_file():
                shutil.copy2(source / name, target / name)


def hindi_card_text(cfg, headline, topic, path):
    """Use libass/HarfBuzz for Indic shaping when Pillow lacks libraqm on Windows."""
    folder = path.parent.resolve()
    local_fonts(cfg, folder)
    scale = cfg.video_width / 1080
    header = (f"[Script Info]\nScriptType: v4.00+\nPlayResX: {cfg.video_width}\n"
              f"PlayResY: {cfg.video_height}\nWrapStyle: 0\n\n[V4+ Styles]\n"
              "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
              "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
              "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
    for name, size, color, bold in (("Title", 82, "&H00FFFFFF", -1), ("Context", 34, "&H00D5BFAD", 0)):
        header += (f"Style: {name},Noto Sans Devanagari,{int(size*scale*1.7)},{color},&H00000000,"
                   f"&H00000000,&H00000000,{bold},0,0,0,100,100,0,0,1,0,0,7,"
                   f"{int(80*scale)},{int(80*scale)},0,1\n")
    header += "\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    for style, text, y in (("Title", headline, 430), ("Context", topic, 900)):
        safe = re.sub(r"[{}\\]", "", text).replace("\n", r"\N")
        header += (f"Dialogue: 0,0:00:00.00,0:00:01.00,{style},,0,0,0,,"
                   f"{{\\an7\\pos({int(80*scale)},{int(y*scale)})}}{safe}\n")
    ass = path.with_name(path.stem + "-title.ass")
    ass.write_text(header, encoding="utf-8-sig")
    shaped = path.with_name(path.stem + "-shaped.png")
    run([cfg.ffmpeg, "-y", "-v", "error", "-i", path.name,
         "-vf", f"ass={ass.name}:fontsdir=.fonts", "-frames:v", "1", shaped.name], cwd=folder)
    shaped.replace(path)


def wrapped(draw, text, face, width):
    lines, line = [], ""
    for word in text.split():
        test = (line + " " + word).strip()
        if draw.textlength(test, font=face) > width and line:
            lines.append(line)
            line = word
        else:
            line = test
    return lines + [line]


def card(cfg, headline, topic, path, index=0, stock=False):
    w, h = cfg.video_width, cfg.video_height
    scale = w / 1080
    img = Image.new("RGB", (w, h), (10, 17, 35))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        draw.line((0, y, w, y), fill=(10 + y*8//h, 17+y*14//h, 35+y*25//h))
    for i in range(12):
        x = int((90+i*83) * scale)
        y = int((420+(i*173+index*71)%800)*scale)
        draw.line((x, 230*scale, x, y, min(w-70*scale, x+100*scale), y),
                  fill=(26, 68, 86), width=max(1, int(3*scale)))
        draw.ellipse((x-6*scale, y-6*scale, x+6*scale, y+6*scale), fill=(40, 211, 195))
    if re.search(r"[\u0900-\u097f]", headline + topic):
        draw.rectangle((80*scale, 810*scale, 300*scale, 818*scale), fill=(38, 223, 201))
        draw.text((80*scale, 1710*scale), "ILLUSTRATIVE STOCK" if stock else "ORIGINAL EXPLAINER GRAPHICS",
                  font=font(max(10, int(24*scale))), fill=(134, 159, 184))
        img.save(path)
        hindi_card_text(cfg, headline, topic, path)
        return
    y = 430*scale
    for line in wrapped(draw, headline.upper(), font(int(82*scale), True), w-160*scale)[:5]:
        draw.text((80*scale, y), line, font=font(int(82*scale), True), fill="white", stroke_width=1)
        y += 105*scale
    draw.rectangle((80*scale, y+35*scale, 300*scale, y+43*scale), fill=(38, 223, 201))
    for n, line in enumerate(wrapped(draw, topic, font(int(34*scale)), w-180*scale)[:3]):
        draw.text((80*scale, y+(90+n*47)*scale), line, font=font(int(34*scale)), fill=(173, 191, 213))
    draw.text((80*scale, 1710*scale), "ILLUSTRATIVE STOCK" if stock else "ORIGINAL EXPLAINER GRAPHICS",
              font=font(max(10, int(24*scale))), fill=(134, 159, 184))
    img.save(path)


def ass_time(seconds):
    centis = round(seconds*100)
    return f"{centis//360000}:{centis//6000%60:02}:{centis//100%60:02}.{centis%100:02}"


def srt_time(seconds):
    millis = round(seconds*1000)
    return f"{millis//3600000:02}:{millis//60000%60:02}:{millis//1000%60:02},{millis%1000:03}"


def subtitles(cfg, segments, folder):
    size = int(cfg.video_width*.068*(1.5 if cfg.narration_language == "hi" else 1))
    family = font(size, True).getname()[0]
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {cfg.video_width}
PlayResY: {cfg.video_height}
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{family},{size},&H00FFFFFF,&H00FFFFFF,&H30000000,&H90000000,-1,0,0,0,100,100,0,0,3,{max(2, int(cfg.video_width*.01))},0,2,{int(cfg.video_width*.12)},{int(cfg.video_width*.12)},{int(cfg.video_height*.23)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events, srt, timings = [], [], []
    for segment in segments:
        tokens = segment["text"].split()
        groups, group = [], []
        measure = ImageDraw.Draw(Image.new("RGB", (1, 1)))
        face = font(size, True)
        for token in tokens:
            if group and (len(group) >= 3 or measure.textlength(" ".join([*group, token]).upper(),
                                                               font=face) > cfg.video_width*.70):
                groups.append(group)
                group = []
            group.append(token)
        if group:
            groups.append(group)
        weights = [sum(max(2, len(t)) for t in group) for group in groups]
        cursor = segment["start"]
        for group, weight in zip(groups, weights):
            end = cursor + (segment["end"]-segment["start"]) * weight/sum(weights)
            plain = " ".join(group).upper().replace("{", "").replace("}", "").replace("\\", "")
            # Phrase timing is estimated within measured TTS scenes; no false word-alignment claim.
            highlighted = "{\\c&HD2E838&}" + plain.split()[0] + "{\\c&HFFFFFF&}"
            text = highlighted + (" " + " ".join(plain.split()[1:]) if len(group) > 1 else "")
            events.append(f"Dialogue: 0,{ass_time(cursor)},{ass_time(end)},Default,,0,0,0,,"
                          "{\\fad(60,60)}" + text)
            srt.append(f"{len(srt)+1}\n{srt_time(cursor)} --> {srt_time(end)}\n{plain}\n")
            timings.append({"start": cursor, "end": end, "text": plain})
            cursor = end
    (folder / "subtitles.ass").write_text(header + "\n".join(events), encoding="utf-8-sig")
    (folder / "subtitles.srt").write_text("\n".join(srt), encoding="utf-8")
    return timings


def stock_labels(cfg, segments, assets, folder):
    """Persist an on-screen label so generic footage cannot appear to be actual event coverage."""
    lines = []
    for segment, asset in zip(segments, assets):
        if asset["kind"] in ("stock", "stock_image"):
            label = (f"{{\\an7\\pos({int(cfg.video_width*.08)},{int(cfg.video_height*.12)})"
                     f"\\fs{int(cfg.video_width*.028*(1.7 if cfg.narration_language == 'hi' else 1))}"
                     "\\c&HFFFFFF&\\bord2}ILLUSTRATIVE STOCK")
            lines.append(f"Dialogue: 1,{ass_time(segment['start'])},{ass_time(segment['end'])},"
                         "Default,,0,0,0,," + label)
    if lines:
        with (folder / "subtitles.ass").open("a", encoding="utf-8") as output:
            output.write("\n" + "\n".join(lines))


def render(cfg, script, segments, assets, folder):
    folder = folder.resolve()
    local_fonts(cfg, folder)
    stock_labels(cfg, segments, assets, folder)
    fps, w, h = cfg.video_fps, cfg.video_width, cfg.video_height
    voice_duration = segments[-1]["end"]
    total = voice_duration
    clips = []
    for i, (scene, segment, asset) in enumerate(zip(script.scenes, segments, assets)):
        seconds = segment["end"] - segment["start"]
        if i == len(segments)-1:
            seconds += total-voice_duration
        frames = math.ceil(seconds * fps)
        card(cfg, scene.headline, script.title, folder / f"scene-{i}.png", i,
             asset["kind"] in ("stock", "stock_image"))
        name = f"clip-{i:02d}.mp4"
        if asset["path"] and asset["kind"] != "stock_image":
            inputs = ["-stream_loop", "-1", "-i", asset["path"]]
            filters = (f"scale={w}:{h}:force_original_aspect_ratio=increase:out_range=tv,"
                       f"crop={w}:{h},setsar=1,"
                       f"fps={fps},fade=t=in:st=0:d=0.12")
        else:
            inputs = ["-i", asset["path"] if asset["path"] else f"scene-{i}.png"]
            filters = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,"
                       f"zoompan=z='min(zoom+0.00035,1.08)':x='iw/2-iw/zoom/2':"
                       f"y='ih/2-ih/zoom/2':d={frames}:s={w}x{h}:fps={fps},"
                       "fade=t=in:st=0:d=0.12,format=yuv420p,setparams=range=limited")
        run([cfg.ffmpeg, "-y", "-v", "error", *inputs, "-vf", filters,
             "-frames:v", str(frames), "-an", "-c:v", "libx264", "-preset", cfg.encoder_preset,
             "-crf", str(cfg.crf), "-threads", "2", "-pix_fmt", "yuv420p", "-color_range", "tv", name], cwd=folder)
        clips.append(name)
    (folder / "concat.txt").write_text("\n".join(f"file '{c}'" for c in clips), encoding="utf-8")
    run([cfg.ffmpeg, "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "concat.txt",
         "-i", "narration.wav", "-vf", "ass=subtitles.ass:fontsdir=.fonts", "-af", "loudnorm=I=-16:TP=-1.5:LRA=9,apad",
         "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", cfg.encoder_preset, "-crf", str(cfg.crf),
         "-threads", "2", "-pix_fmt", "yuv420p", "-color_range", "tv", "-c:a", "aac", "-b:a", "160k", "-ar", "48000",
         "-movflags", "+faststart", "final.mp4"], cwd=folder)
    card(cfg, script.thumbnail_text, script.title, folder / "cover.png")
    with Image.open(folder / "cover.png") as cover:
        cover.resize((720, 1280)).save(folder / "thumbnail.png")
    return folder / "final.mp4"


def quality(cfg, path, timings, narration):
    info = probe(cfg, path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), {})
    audio = next((s for s in info["streams"] if s["codec_type"] == "audio"), {})
    duration = float(info["format"]["duration"])
    num, den = map(int, video.get("avg_frame_rate", "0/1").split("/"))
    checks = {"dimensions": (video.get("width"), video.get("height")) ==
              (cfg.video_width, cfg.video_height), "video_codec": video.get("codec_name") == "h264",
              "pixel_format": video.get("pix_fmt") == "yuv420p", "audio_codec": audio.get("codec_name") == "aac",
              "fps": abs(num/max(1, den)-cfg.video_fps) < .1,
              "duration": cfg.duration_min <= duration <= cfg.duration_max,
              "subtitles": bool(timings) and timings[-1]["end"] <= duration+.1
              and all(0 <= t["start"] < t["end"] <= duration+.1 for t in timings)}
    check_audio(narration)
    scan = run([cfg.ffmpeg, "-hide_banner", "-i", path, "-vf", "blackdetect=d=1:pix_th=0.10",
                "-af", "silencedetect=n=-50dB:d=2,astats=metadata=0:reset=0", "-f", "null", "-"],
               timeout=900).stderr
    black = [float(x) for x in re.findall(r"black_duration:([\d.]+)", scan)]
    silence = [float(x) for x in re.findall(r"silence_duration: ([\d.]+)", scan)]
    peaks = [float(x) for x in re.findall(r"Peak level dB: (-?[\d.]+)", scan)]
    checks.update(not_black=sum(black) < duration*.25, not_silent=sum(silence) < duration*.25,
                  decoded_peak=bool(peaks) and max(peaks) < -.05)
    result = {"passed": all(checks.values()), "checks": checks, "duration": duration,
              "caption_alignment": "phrase estimates within measured scene durations",
              "narration": check_audio(narration)}
    if not result["passed"]:
        raise ValueError("Video quality rejected: " + ", ".join(k for k, ok in checks.items() if not ok))
    return result
