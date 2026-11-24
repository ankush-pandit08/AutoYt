"""Captions & Subtitles Studio.

Enables uploading 60s Shorts/Reels and burning animated, word-level viral subtitles
with popular creator fonts (Arial Black, Montserrat, Bebas Neue, Anton, Poppins, Rubik, Impact)
and customizable animation styles (Hormozi Yellow Pop, MrBeast Lime, Neon Mint, Ali Abdaal Minimal, etc.).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .podcast.transcription import Transcriber, WordTimestamp
from .podcast.utils import get_ffmpeg_binary

log = logging.getLogger(__name__)

# Color hex constants in ASS BGR format (&HAABBGGRR&)
COLOR_WHITE = "&H00FFFFFF&"
COLOR_YELLOW = "&H0000FFFF&"       # B=00, G=FF, R=FF
COLOR_LIME = "&H0000FF00&"         # B=00, G=FF, R=00
COLOR_MINT = "&H00D4C100&"         # B=D4, G=C1, R=00 (Teal/Mint)
COLOR_ORANGE = "&H000080FF&"       # B=00, G=80, R=FF
COLOR_GOLD = "&H0024BFFB&"         # B=24, G=BF, R=FB (Golden)
COLOR_BLACK = "&H00000000&"

FONTS_CATALOG = {
    "Arial Black": {"family": "Arial Black", "label": "Arial Black (Hormozi Classic Heavy)"},
    "Montserrat": {"family": "Montserrat", "label": "Montserrat ExtraBold (Modern Tech & Lifestyle)"},
    "Bebas Neue": {"family": "Bebas Neue", "label": "Bebas Neue (Tall Cinematic Condensed)"},
    "Anton": {"family": "Anton", "label": "Anton (Bold Punchy Urban)"},
    "Poppins": {"family": "Poppins", "label": "Poppins Bold (Friendly Modern Creator)"},
    "Rubik": {"family": "Rubik", "label": "Rubik Bold (Punchy Soft Rounded)"},
    "Impact": {"family": "Impact", "label": "Impact (Classic Viral High-Contrast)"},
    "Segoe UI": {"family": "Segoe UI", "label": "Segoe UI Bold (Clean Minimal Sans)"},
}

STYLES_CATALOG = {
    "hormozi_yellow": {
        "name": "🔥 Hormozi Yellow Pop",
        "description": "Active word highlights in bright electric yellow with 115% zoom pop and heavy black border",
        "highlight_color": COLOR_YELLOW,
        "base_color": COLOR_WHITE,
        "zoom": True,
        "border_width": 6,
        "shadow_width": 3,
        "uppercase": True,
    },
    "mrbeast_green": {
        "name": "⚡ MrBeast Lime Pop",
        "description": "High-energy lime green active word highlight with punchy contrast",
        "highlight_color": COLOR_LIME,
        "base_color": COLOR_WHITE,
        "zoom": True,
        "border_width": 6,
        "shadow_width": 3,
        "uppercase": True,
    },
    "neon_mint": {
        "name": "💎 Neon Mint Glow",
        "description": "Electric cyan/mint active word accent on crisp ice-white typography",
        "highlight_color": COLOR_MINT,
        "base_color": COLOR_WHITE,
        "zoom": True,
        "border_width": 5,
        "shadow_width": 2,
        "uppercase": True,
    },
    "fire_red": {
        "name": "🔴 Crimson Fire Pop",
        "description": "Fiery orange/red active word pop for high-urgency hook statements",
        "highlight_color": COLOR_ORANGE,
        "base_color": COLOR_WHITE,
        "zoom": True,
        "border_width": 6,
        "shadow_width": 3,
        "uppercase": True,
    },
    "gold_luxury": {
        "name": "👑 Devon Gold Luxury",
        "description": "Warm golden amber active word accent, sleek documentary style",
        "highlight_color": COLOR_GOLD,
        "base_color": COLOR_WHITE,
        "zoom": False,
        "border_width": 5,
        "shadow_width": 2,
        "uppercase": False,
    },
    "clean_minimal": {
        "name": "🤍 Ali Abdaal Minimal",
        "description": "Clean white text with subtle dark shadow, high readability, natural casing",
        "highlight_color": COLOR_WHITE,
        "base_color": COLOR_WHITE,
        "zoom": False,
        "border_width": 3,
        "shadow_width": 2,
        "uppercase": False,
    },
}


def _format_ass_time(seconds: float) -> str:
    """Format float seconds to ASS timestamp H:MM:SS.cc"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis >= 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


class SubtitlesStudio:
    def __init__(self, base_dir: Path | None = None):
        self.base_dir = (base_dir or Path("output/subtitles_studio")).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.transcriber = Transcriber(model_size="base")
        self.ffmpeg = get_ffmpeg_binary()

    def list_jobs(self) -> List[Dict[str, Any]]:
        """List past subtitled jobs sorted by creation time descending."""
        jobs = []
        for d in self.base_dir.iterdir():
            if d.is_dir():
                meta_file = d / "meta.json"
                if meta_file.is_file():
                    try:
                        jobs.append(json.loads(meta_file.read_text(encoding="utf-8")))
                    except Exception:
                        pass
        jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
        return jobs

    def save_uploaded_file(self, file_bytes: bytes, original_filename: str) -> str:
        """Save an uploaded video file to a unique job directory."""
        job_id = secrets.token_hex(8)
        job_dir = self.base_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)

        ext = Path(original_filename).suffix.lower()
        if ext not in (".mp4", ".mov", ".webm", ".m4v", ".mkv"):
            ext = ".mp4"
        input_path = job_dir / f"input{ext}"
        input_path.write_bytes(file_bytes)

        meta = {
            "id": job_id,
            "filename": original_filename,
            "input_path": str(input_path),
            "created_at": time.time(),
            "status": "uploaded",
        }
        (job_dir / "meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return job_id

    def process_subtitles(
        self,
        job_id: str,
        font: str = "Arial Black",
        style_key: str = "hormozi_yellow",
        position: str = "middle",
        chunk_size: int = 3,
        text_case: str = "upper",
        font_size: int = 76,
    ) -> Dict[str, Any]:
        """Process transcription, build ASS subtitles, and render subtitled video."""
        job_dir = self.base_dir / job_id
        if not job_dir.is_dir():
            raise ValueError(f"Job {job_id} not found")

        meta_file = job_dir / "meta.json"
        meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.is_file() else {}

        input_files = list(job_dir.glob("input.*"))
        if not input_files:
            raise ValueError("Input video not found")
        input_video = input_files[0]

        # 1. Extract audio to 16kHz WAV for faster-whisper
        audio_path = job_dir / "audio.wav"
        if not audio_path.is_file():
            log.info("Extracting audio from %s -> %s", input_video, audio_path)
            cmd_audio = [
                str(self.ffmpeg),
                "-y",
                "-i", str(input_video),
                "-vn",
                "-acodec", "pcm_s16le",
                "-ar", "16000",
                "-ac", "1",
                str(audio_path),
            ]
            subprocess.run(cmd_audio, check=True, capture_output=True)

        # 2. Transcribe with word timestamps
        transcript_cache = job_dir / "transcript.json"
        transcript = self.transcriber.transcribe(audio_path, job_id, cache_file=transcript_cache)

        # Extract all words
        words: List[WordTimestamp] = []
        for seg in transcript.segments:
            words.extend(seg.words)

        # 3. Generate Styled ASS file
        ass_path = job_dir / "subtitles.ass"
        self._build_ass_file(
            words=words,
            output_ass=ass_path,
            font_name=font,
            style_key=style_key,
            position=position,
            chunk_size=chunk_size,
            text_case=text_case,
            font_size=font_size,
        )

        # 4. Burn ASS Subtitles onto Video using FFmpeg
        output_video = job_dir / "subtitled.mp4"
        rel_ass = ass_path.as_posix().replace(":", r"\:")
        
        # Windows libass path escaping: use relative path if possible to avoid drive letter issues
        try:
            rel_ass_path = ass_path.relative_to(Path.cwd()).as_posix()
        except Exception:
            rel_ass_path = rel_ass

        vf_filter = f"ass=filename='{rel_ass_path}':fontsdir=data/fonts"
        cmd_render = [
            str(self.ffmpeg),
            "-y",
            "-i", str(input_video),
            "-vf", vf_filter,
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "copy",
            str(output_video),
        ]
        log.info("Rendering subtitled video: %s", " ".join(cmd_render))
        res = subprocess.run(cmd_render, capture_output=True, text=True)
        if res.returncode != 0:
            log.error("FFmpeg error burning subtitles: %s", res.stderr)
            raise RuntimeError(f"FFmpeg failed to burn subtitles: {res.stderr[-400:]}")

        # 5. Generate Thumbnail
        thumb_path = job_dir / "thumbnail.png"
        cmd_thumb = [
            str(self.ffmpeg),
            "-y",
            "-ss", "00:00:01",
            "-i", str(output_video),
            "-vframes", "1",
            str(thumb_path),
        ]
        subprocess.run(cmd_thumb, capture_output=True)

        # 6. Update metadata
        meta.update({
            "status": "ready",
            "font": font,
            "style_key": style_key,
            "position": position,
            "font_size": font_size,
            "words_count": len(words),
            "duration": transcript.duration_seconds,
            "output_path": str(output_video),
            "thumbnail_path": str(thumb_path),
            "video_url": f"/media/subtitles_studio/{job_id}/subtitled.mp4",
            "thumb_url": f"/media/subtitles_studio/{job_id}/thumbnail.png",
            "download_url": f"/media/subtitles_studio/{job_id}/subtitled.mp4?download=1",
            "updated_at": time.time(),
        })
        meta_file.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        return meta

    def _build_ass_file(
        self,
        words: List[WordTimestamp],
        output_ass: Path,
        font_name: str,
        style_key: str,
        position: str,
        chunk_size: int,
        text_case: str,
        font_size: int,
    ):
        """Construct the complete ASS file with word-by-word timing and styling."""
        style = STYLES_CATALOG.get(style_key, STYLES_CATALOG["hormozi_yellow"])
        highlight_color = style["highlight_color"]
        base_color = style["base_color"]
        zoom = style["zoom"]
        border_width = style["border_width"]
        shadow_width = style["shadow_width"]

        # Position alignment & vertical margin:
        # alignment 2 = Bottom-center (standard marginV=520 from bottom)
        # alignment 5 = Middle-center (marginV=0 or small offset)
        # alignment 8 = Top-center (marginV=300)
        if position == "middle":
            alignment = 5
            margin_v = 0
        elif position == "top":
            alignment = 8
            margin_v = 340
        else:  # bottom
            alignment = 2
            margin_v = 520

        header = f"""[Script Info]
Title: AutoYT Reels Subtitler
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{base_color},&H000000FF,&H00000000,&H80000000,1,0,0,0,100,100,0,0,1,{border_width},{shadow_width},{alignment},60,60,{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
        # Chunk words into punchy phrases
        phrases: List[List[WordTimestamp]] = []
        curr: List[WordTimestamp] = []
        for w in words:
            cleaned = w.word.strip()
            if not cleaned:
                continue
            curr.append(w)
            if len(curr) >= chunk_size or (w.word and w.word[-1] in ".!?,;:"):
                phrases.append(curr)
                curr = []
        if curr:
            phrases.append(curr)

        events: List[str] = []
        for phrase in phrases:
            # Word-by-word active highlight
            for current_idx, active_word in enumerate(phrase):
                w_start = _format_ass_time(active_word.start)
                w_end = _format_ass_time(active_word.end)

                tokens = []
                for i, w in enumerate(phrase):
                    clean_w = w.word.strip().replace("{", "").replace("}", "")
                    if text_case == "upper":
                        clean_w = clean_w.upper()
                    elif text_case == "title":
                        clean_w = clean_w.title()

                    if i == current_idx:
                        if zoom:
                            tokens.append(f"{{\\c{highlight_color}\\b1\\fscx114\\fscy114}}{clean_w}{{\\r}}")
                        else:
                            tokens.append(f"{{\\c{highlight_color}\\b1}}{clean_w}{{\\r}}")
                    else:
                        tokens.append(f"{{\\c{base_color}}}{clean_w}")
                line_text = " ".join(tokens)
                events.append(f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{line_text}")

        content = header + "\n".join(events) + "\n"
        output_ass.write_text(content, encoding="utf-8")
        log.info("Built ASS subtitle file (%d phrases, font=%s, style=%s) -> %s", len(phrases), font_name, style_key, output_ass)
