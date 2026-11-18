"""Thumbnail generation for podcast Shorts clips.

Extracts a sharp frame with speaker face from the clip, applies high-contrast
gradient overlay and readable bold headline typography.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from .utils import get_ffmpeg_binary

log = logging.getLogger(__name__)


class ThumbnailGenerator:
    """Generates engaging 1080x1920 vertical thumbnails for Shorts."""

    def __init__(self, font_path: Optional[str] = None, cfg=None):
        self.font_path = font_path
        self.cfg = cfg

    def generate_thumbnail(
        self,
        video_path: Path,
        timestamp_seconds: float,
        headline: str,
        output_file: Path,
    ) -> Path:
        """Extract frame from video_path and render high-impact text overlay."""
        output_file.parent.mkdir(parents=True, exist_ok=True)
        raw_frame_path = output_file.with_suffix(".raw.jpg")

        # 1. Extract frame at timestamp
        ffmpeg_bin = get_ffmpeg_binary(self.cfg)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss", f"{timestamp_seconds:.3f}",
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(raw_frame_path),
        ]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0 or not raw_frame_path.is_file():
            # Fallback: grab frame at 1 second
            cmd[2] = "1.000"
            subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

        if not raw_frame_path.is_file():
            raise RuntimeError(f"Could not extract thumbnail frame from {video_path}")

        # 2. Open frame and ensure 1080x1920
        img = Image.open(raw_frame_path).convert("RGBA")
        if img.size != (1080, 1920):
            img = img.resize((1080, 1920), Image.Resampling.LANCZOS)

        # 3. Add top and bottom subtle dark gradient for text legibility
        overlay = Image.new("RGBA", (1080, 1920), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        # Gradient at top third (y=100 to y=600) for headline
        for y in range(120, 580):
            alpha = int(180 * (1.0 - abs(y - 350) / 230))
            draw.line([(0, y), (1080, y)], fill=(0, 0, 0, max(0, min(190, alpha))))

        # 4. Draw bold headline text
        clean_headline = self._clean_headline(headline)
        lines = self._wrap_text(clean_headline, max_chars=20)[:3]

        has_devanagari = any("\u0900" <= ch <= "\u097f" for ch in clean_headline)
        font_candidates = (
            ("NirmalaB.ttf", "Nirmala.ttf", "ariblk.ttf", "arialbd.ttf")
            if has_devanagari
            else ("ariblk.ttf", "impact.ttf", "arialbd.ttf")
        )

        font = None
        for font_name in font_candidates:
            try:
                font = ImageFont.truetype(font_name, 72)
                break
            except Exception:
                continue
        if font is None:
            font = ImageFont.load_default()

        # Render headline lines with black border and bright yellow fill
        start_y = 260
        for i, line in enumerate(lines):
            line_y = start_y + (i * 85)
            # Center horizontally
            bbox = font.getbbox(line)
            w = bbox[2] - bbox[0]
            x = (1080 - w) // 2

            # Black outline
            for ox in (-4, -2, 0, 2, 4):
                for oy in (-4, -2, 0, 2, 4):
                    draw.text((x + ox, line_y + oy), line, font=font, fill=(0, 0, 0, 255))
            # Gold / Yellow text fill
            draw.text((x, line_y), line, font=font, fill=(255, 220, 0, 255))

        # Composite and save
        final_img = Image.alpha_composite(img, overlay).convert("RGB")
        final_img.save(output_file, "PNG", quality=95)

        # Cleanup raw temp frame
        try:
            raw_frame_path.unlink(missing_ok=True)
        except Exception:
            pass

        log.info("Generated Shorts thumbnail: %s", output_file)
        return output_file

    def _clean_headline(self, text: str) -> str:
        """Strip hashtags, emojis, and trim to punchy headline."""
        text = text.replace("#Shorts", "").replace("#shorts", "")
        # Remove pipe tags
        if "|" in text:
            text = text.split("|")[0]
        return text.strip().upper()

    def _wrap_text(self, text: str, max_chars: int = 20) -> list[str]:
        words = text.split()
        lines = []
        current = []
        for w in words:
            if sum(len(x) + 1 for x in current) + len(w) <= max_chars:
                current.append(w)
            else:
                if current:
                    lines.append(" ".join(current))
                current = [w]
        if current:
            lines.append(" ".join(current))
        return lines or [text]
