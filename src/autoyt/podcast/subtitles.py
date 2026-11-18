"""Advanced ASS and SRT Subtitle Generator for 9:16 vertical Shorts.

Supports three distinct styles:
- minimal: Clean, subtle shadow, professional white typography.
- dynamic: Animated word highlights with bright accent color.
- bold: High-impact uppercase motivational text with heavy black borders.

Positions subtitles strictly within YouTube Shorts safe areas (above the bottom UI and below top overlay).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import List

from .transcription import WordTimestamp
from .utils import clean_to_english_hinglish

log = logging.getLogger(__name__)


def _format_ass_time(seconds: float) -> str:
    """Format float seconds to ASS timestamp H:MM:SS.cc"""
    hrs = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    centis = int(round((seconds - int(seconds)) * 100))
    if centis == 100:
        centis = 99
    return f"{hrs}:{mins:02d}:{secs:02d}.{centis:02d}"


class SubtitleGenerator:
    """Generates styled ASS subtitle files for vertical Shorts (1080x1920)."""

    def __init__(self, style: str = "dynamic"):
        self.style = style.lower() if style in ("minimal", "dynamic", "bold") else "dynamic"

    def generate_ass(
        self,
        words: List[WordTimestamp],
        clip_start: float,
        output_file: Path,
        style: str | None = None,
    ) -> Path:
        """Create an ASS subtitle file from relative-timed words."""
        active_style = (style or self.style).lower()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Normalize word timestamps relative to clip start and sanitize strictly to English/Hinglish
        rel_words = []
        for w in words:
            cleaned_word = clean_to_english_hinglish(w.word, keep_emojis=False) or w.word
            rel_words.append(
                WordTimestamp(
                    word=cleaned_word,
                    start=max(0.0, w.start - clip_start),
                    end=max(0.0, w.end - clip_start),
                    probability=w.probability,
                )
            )

        # Group words into 2-3 word readable punchy phrase lines (Raj Shamani / Shorts style)
        phrases = self._group_words_into_phrases(rel_words, max_words=3)

        # Use bold, impactful font for English & Romanized Hinglish subtitles
        font_name = "Arial Black"

        # Generate ASS Header and Styles
        header = self._build_ass_header(active_style, font_name=font_name)
        events = ["[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]

        for phrase in phrases:
            p_start = phrase[0].start
            p_end = phrase[-1].end
            start_str = _format_ass_time(p_start)
            end_str = _format_ass_time(p_end)

            if active_style == "dynamic":
                # Word-by-word active highlight within phrase in bold uppercase
                for current_idx, active_word in enumerate(phrase):
                    w_start = _format_ass_time(active_word.start)
                    w_end = _format_ass_time(active_word.end)

                    tokens = []
                    for i, w in enumerate(phrase):
                        clean_w = w.word.upper().replace("{", "").replace("}", "")
                        if i == current_idx:
                            # Highlight active word with brilliant electric yellow & subtle zoom pop
                            tokens.append(f"{{\\c&H0000FFFF&\\b1\\fscx112\\fscy112}}{clean_w}{{\\r}}")
                        else:
                            tokens.append(f"{{\\c&H00FFFFFF&}}{clean_w}")
                    line_text = " ".join(tokens)
                    events.append(f"Dialogue: 0,{w_start},{w_end},Default,,0,0,0,,{line_text}")

            elif active_style == "bold":
                # High-impact uppercase motivational text in bold electric yellow
                clean_text = " ".join(w.word.upper() for w in phrase).replace("{", "").replace("}", "")
                events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{clean_text}")

            else:  # minimal
                clean_text = " ".join(w.word for w in phrase).replace("{", "").replace("}", "")
                events.append(f"Dialogue: 0,{start_str},{end_str},Default,,0,0,0,,{clean_text}")

        content = header + "\n" + "\n".join(events) + "\n"
        output_file.write_text(content, encoding="utf-8")
        log.info("Generated %s subtitles with %d phrases: %s", active_style, len(phrases), output_file)
        return output_file

    def _group_words_into_phrases(
        self, words: List[WordTimestamp], max_words: int = 4
    ) -> List[List[WordTimestamp]]:
        """Chunk a sequence of words into brief visual phrases."""
        if not words:
            return []

        phrases = []
        current = []
        for w in words:
            current.append(w)
            # Break if reaching max words or on sentence-ending punctuation
            if len(current) >= max_words or (w.word and w.word[-1] in ".!?,;:"):
                phrases.append(current)
                current = []
        if current:
            phrases.append(current)
        return phrases

    def _build_ass_header(self, style_name: str, font_name: str = "Arial Black") -> str:
        """Build ASS file header with 1080x1920 layout and style definition."""
        # 1080x1920 canvas
        # MarginV=520 places subtitles in the visual sweet spot, above Shorts bottom titles/actions
        if style_name == "bold":
            # Extra bold, heavy outline, bright yellow/white contrast
            style_def = (
                f"Style: Default,{font_name},76,&H0000FFFF,&H000000FF,&H00000000,&H80000000,"
                "1,0,0,0,100,100,0,0,1,7,4,2,60,60,520,1"
            )
        elif style_name == "minimal":
            # Clean typography, subtle dark background box
            style_def = (
                f"Style: Default,{font_name},58,&H00FFFFFF,&H000000FF,&H00000000,&HA0000000,"
                "1,0,0,0,100,100,0,0,1,4,2,2,80,80,500,1"
            )
        else:  # dynamic (signature bold look)
            # Bold font with thick black border and electric yellow highlight pop
            style_def = (
                f"Style: Default,{font_name},72,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,"
                "1,0,0,0,100,100,0,0,1,6,3,2,60,60,520,1"
            )

        return f"""[Script Info]
Title: AutoYT Shorts Subtitles
ScriptType: v4.00+
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709
PlayResX: 1080
PlayResY: 1920

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{style_def}
"""
