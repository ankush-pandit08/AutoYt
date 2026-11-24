"""Automated Shorts Video Editor.

Reframes 16:9 podcast video into 1080x1920 (9:16) vertical Shorts.
Features:
- Face detection and speaker centering with OpenCV.
- Jitter-free smooth crop positioning.
- EBU R128 broadcast audio loudness normalization.
- Subtitle burning (ASS).
- 30 FPS H.264 / AAC rendering.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import Optional

from .music import MusicLibrary, DEFAULT_BGM_VOLUME
from .utils import get_ffmpeg_binary, get_ffprobe_binary

log = logging.getLogger(__name__)


class ClipEditor:
    """Renders 9:16 vertical Shorts from source video and timestamped subtitles."""

    def __init__(
        self,
        target_width: int = 1080,
        target_height: int = 1920,
        fps: int = 30,
        cfg=None,
    ):
        self.target_width = target_width
        self.target_height = target_height
        self.fps = fps
        self.cfg = cfg
        self.music_lib = MusicLibrary()

    def render_clip(
        self,
        source_video: Path,
        start_seconds: float,
        duration_seconds: float,
        subtitles_ass: Optional[Path],
        output_file: Path,
        layout: str = "auto",
        vibe: Optional[str] = "motivational",
        music_path: Optional[Path] = None,
        bgm_volume: float = DEFAULT_BGM_VOLUME,
        enable_bgm: bool = True,
    ) -> Path:
        """Render a vertical 9:16 Short clip with optional vibe-matched background music.

        Layout options:
        - "auto": Checks for dual speakers on left/right. If detected, creates split-screen; otherwise face-centered single speaker.
        - "single": Standard face-centered vertical crop (1080x1920).
        - "split_screen": Stacked dual-speaker vertical layout (top half left speaker, bottom half right speaker).
        """
        output_file.parent.mkdir(parents=True, exist_ok=True)
        log.info(
            "Rendering clip from %s [%.1fs to %.1fs, dur=%.1fs, layout=%s, vibe=%s] -> %s",
            source_video, start_seconds, start_seconds + duration_seconds, duration_seconds, layout, vibe, output_file
        )

        use_split, left_x, right_x = self._detect_dual_speakers(source_video, start_seconds, duration_seconds)
        if layout == "split_screen" or (layout == "auto" and use_split):
            log.info("Applying multi-speaker vertical split-screen (left_x=%d, right_x=%d)", left_x, right_x)
            v_filter = self._build_split_screen_filter(source_video, left_x, right_x, subtitles_ass)
        else:
            # 1. Determine optimal horizontal crop offset using face detection
            crop_x, crop_w, crop_h = self._compute_crop_window(source_video, start_seconds, duration_seconds)
            filters = [
                f"[0:v]crop={crop_w}:{crop_h}:{crop_x}:0",
                f"scale={self.target_width}:{self.target_height}:flags=bicubic",
                f"fps={self.fps}",
            ]
            if subtitles_ass and subtitles_ass.is_file():
                try:
                    rel_ass = Path(subtitles_ass).resolve().relative_to(Path.cwd()).as_posix()
                except Exception:
                    rel_ass = subtitles_ass.resolve().as_posix().replace(":", r"\:")
                filters.append(f"ass='{rel_ass}':fontsdir='data/fonts'")
            v_filter = ",".join(filters) + "[v_out]"

        # 2. Audio filter & background music resolution
        resolved_music: Optional[Path] = None
        if enable_bgm:
            if music_path and Path(music_path).is_file():
                resolved_music = Path(music_path)
            else:
                resolved_music = self.music_lib.get_track_for_vibe(vibe)

        if resolved_music and resolved_music.is_file():
            log.info("Mixing background music (%s, vibe=%s, vol=%.2f) with ducking", resolved_music.name, vibe, bgm_volume)
            fade_out_st = max(0.1, duration_seconds - 1.2)
            a_filter = (
                f"[1:a]volume={bgm_volume:.3f},afade=t=in:ss=0:d=0.5,afade=t=out:st={fade_out_st:.3f}:d=1.2[bgm]; "
                f"[0:a][bgm]amix=inputs=2:duration=first:dropout_transition=2,loudnorm=I=-16:TP=-1.5:LRA=11[a_out]"
            )
        else:
            a_filter = "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[a_out]"

        complex_filter = f"{v_filter}; {a_filter}"

        ffmpeg_bin = get_ffmpeg_binary(self.cfg)
        cmd = [
            ffmpeg_bin,
            "-y",
            "-ss", f"{start_seconds:.3f}",
            "-t", f"{duration_seconds:.3f}",
            "-i", str(source_video),
        ]

        if resolved_music and resolved_music.is_file():
            cmd.extend(["-t", f"{duration_seconds:.3f}", "-stream_loop", "-1", "-i", str(resolved_music)])

        cmd.extend([
            "-filter_complex", complex_filter,
            "-map", "[v_out]",
            "-map", "[a_out]",
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "22",
            "-threads", "2",
            "-c:a", "aac",
            "-b:a", "192k",
            "-ar", "48000",
            "-movflags", "+faststart",
            str(output_file),
        ])

        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode != 0 or not output_file.is_file() or output_file.stat().st_size == 0:
            log.error("FFmpeg render failed: %s", proc.stderr[-1000:])
            raise RuntimeError(f"FFmpeg rendering failed: {proc.stderr[-500:]}")

        log.info("Successfully rendered Short (size=%.2f MB): %s", output_file.stat().st_size / (1024 * 1024), output_file)
        return output_file

    def _compute_crop_window(
        self, video_path: Path, start_seconds: float, duration_seconds: float
    ) -> tuple[int, int, int]:
        """Compute horizontal crop (x, width, height) to frame the speaker face in 9:16."""
        # Get video resolution
        width, height = self._get_video_resolution(video_path)
        if width == 0 or height == 0:
            width, height = 1920, 1080

        # For 9:16, crop width is height * 9 / 16
        crop_h = height
        crop_w = int(height * 9 / 16)
        if crop_w % 2 != 0:
            crop_w -= 1

        # Fallback center crop
        default_x = max(0, (width - crop_w) // 2)

        try:
            import cv2

            if hasattr(cv2, "CascadeClassifier"):
                # Sample 5 frames across the clip to locate face center
                face_centers = []
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(cascade_path)

                cap = cv2.VideoCapture(str(video_path))
                sample_times = [
                    start_seconds + duration_seconds * frac
                    for frac in (0.15, 0.35, 0.50, 0.65, 0.85)
                ]

                for t in sample_times:
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(60, 60))
                    if len(faces) > 0:
                        # Pick largest face
                        faces = sorted(faces, key=lambda f: f[2] * f[3], reverse=True)
                        fx, fy, fw, fh = faces[0]
                        face_centers.append(fx + fw / 2)

                cap.release()

                if face_centers:
                    # Average face center
                    avg_face_x = sum(face_centers) / len(face_centers)
                    # Position crop so face is roughly in center horizontally
                    calc_x = int(avg_face_x - (crop_w / 2))
                    # Clamp within boundaries
                    crop_x = max(0, min(width - crop_w, calc_x))
                    log.info("Face detected at x=%.1f; selected crop_x=%d (default=%d)", avg_face_x, crop_x, default_x)
                    return crop_x, crop_w, crop_h

        except Exception as exc:
            log.warning("OpenCV face tracking fallback to center crop: %s", exc)

        log.info("Using centered 9:16 crop at x=%d (w=%d, h=%d)", default_x, crop_w, crop_h)
        return default_x, crop_w, crop_h

    def _detect_dual_speakers(
        self, video_path: Path, start_seconds: float, duration_seconds: float
    ) -> tuple[bool, int, int]:
        """Detect whether clip has two distinct speakers on the left and right halves."""
        width, height = self._get_video_resolution(video_path)
        mid_x = width // 2
        default_left = max(0, mid_x // 2 - 270)
        default_right = min(width - 540, mid_x + mid_x // 2 - 270)

        try:
            import cv2
            if hasattr(cv2, "CascadeClassifier"):
                cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
                face_cascade = cv2.CascadeClassifier(cascade_path)
                cap = cv2.VideoCapture(str(video_path))

                left_faces, right_faces = [], []
                sample_times = [
                    start_seconds + duration_seconds * frac
                    for frac in (0.2, 0.4, 0.6, 0.8)
                ]

                for t in sample_times:
                    cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                    ret, frame = cap.read()
                    if not ret or frame is None:
                        continue
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, scaleFactor=1.15, minNeighbors=4, minSize=(60, 60))
                    for fx, fy, fw, fh in faces:
                        center_x = fx + fw / 2
                        if center_x < mid_x * 0.9:
                            left_faces.append(center_x)
                        elif center_x > mid_x * 1.1:
                            right_faces.append(center_x)

                cap.release()
                if len(left_faces) >= 2 and len(right_faces) >= 2:
                    avg_left = sum(left_faces) / len(left_faces)
                    avg_right = sum(right_faces) / len(right_faces)
                    crop_w = int(height * 9 / 16)
                    left_x = max(0, min(mid_x - 100, int(avg_left - crop_w / 2)))
                    right_x = max(mid_x, min(width - crop_w, int(avg_right - crop_w / 2)))
                    return True, left_x, right_x
        except Exception as exc:
            log.debug("Dual speaker detection skipped: %s", exc)

        return False, default_left, default_right

    def _build_split_screen_filter(
        self, video_path: Path, left_x: int, right_x: int, subtitles_ass: Optional[Path]
    ) -> str:
        """Create FFmpeg filter for vertical dual-speaker split screen."""
        _, height = self._get_video_resolution(video_path)
        crop_h = height
        crop_w = int(height * 9 / 16)
        if crop_w % 2 != 0:
            crop_w -= 1

        half_h = self.target_height // 2  # 960

        filter_parts = [
            f"[0:v]crop={crop_w}:{crop_h}:{left_x}:0,scale={self.target_width}:{half_h}:flags=bicubic[top]",
            f"[0:v]crop={crop_w}:{crop_h}:{right_x}:0,scale={self.target_width}:{half_h}:flags=bicubic[bot]",
            f"[top][bot]vstack=inputs=2[v_split]",
        ]

        if subtitles_ass and subtitles_ass.is_file():
            try:
                rel_ass = Path(subtitles_ass).resolve().relative_to(Path.cwd()).as_posix()
            except Exception:
                rel_ass = subtitles_ass.resolve().as_posix().replace(":", r"\:")
            filter_parts.append(f"[v_split]ass='{rel_ass}':fontsdir='data/fonts',fps={self.fps}[v_out]")
        else:
            filter_parts.append(f"[v_split]fps={self.fps}[v_out]")

        return ";".join(filter_parts)

    def _get_video_resolution(self, video_path: Path) -> tuple[int, int]:
        """Query video width and height using ffprobe."""
        ffprobe_bin = get_ffprobe_binary(self.cfg)
        cmd = [
            ffprobe_bin,
            "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=width,height",
            "-of", "csv=s=x:p=0",
            str(video_path),
        ]
        try:
            proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            w, h = map(int, proc.stdout.strip().split("x"))
            return w, h
        except Exception:
            return 1920, 1080
