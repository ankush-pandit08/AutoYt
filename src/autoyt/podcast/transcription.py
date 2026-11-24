"""Audio transcription pipeline using faster-whisper with word-level timestamps."""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Optional

# Prevent Intel MKL memory allocation exhaustion on Windows
os.environ.setdefault("CT2_PACKED_GEMM", "0")
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

from .utils import clean_to_english_hinglish

log = logging.getLogger(__name__)


@dataclass
class WordTimestamp:
    word: str
    start: float
    end: float
    probability: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TranscriptSegment:
    id: int
    start: float
    end: float
    text: str
    words: List[WordTimestamp]
    speaker: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["words"] = [w.to_dict() if isinstance(w, WordTimestamp) else w for w in self.words]
        return d


@dataclass
class FullTranscript:
    episode_id: str
    duration_seconds: float
    language: str
    segments: List[TranscriptSegment]

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "duration_seconds": self.duration_seconds,
            "language": self.language,
            "segments": [s.to_dict() for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FullTranscript":
        segs = []
        for s in data.get("segments", []):
            words = [
                WordTimestamp(
                    word=clean_to_english_hinglish(w["word"], keep_emojis=False) or w["word"],
                    start=float(w["start"]),
                    end=float(w["end"]),
                    probability=float(w.get("probability", 1.0)),
                )
                for w in s.get("words", [])
            ]
            clean_seg_text = clean_to_english_hinglish(s.get("text", ""), keep_emojis=False) or s.get("text", "")
            segs.append(
                TranscriptSegment(
                    id=s["id"],
                    start=float(s["start"]),
                    end=float(s["end"]),
                    text=clean_seg_text,
                    words=words,
                    speaker=s.get("speaker"),
                )
            )
        return cls(
            episode_id=data["episode_id"],
            duration_seconds=float(data["duration_seconds"]),
            language=data.get("language", "en"),
            segments=segs,
        )


class Transcriber:
    """faster-whisper transcriber producing word-level timestamps."""

    def __init__(self, model_size: str = "base", device: str = "cpu", compute_type: str = "int8"):
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            log.info(
                "Loading faster-whisper model '%s' on %s (%s)...",
                self.model_size,
                self.device,
                self.compute_type,
            )
            self._model = WhisperModel(
                self.model_size,
                device=self.device,
                compute_type=self.compute_type,
                cpu_threads=4,
            )
        return self._model

    def transcribe(self, audio_path: Path, episode_id: str, cache_file: Optional[Path] = None) -> FullTranscript:
        """Transcribe an audio WAV file.

        If cache_file exists, loads from cache. Otherwise runs model and saves to cache_file.
        """
        if cache_file and cache_file.is_file():
            try:
                data = json.loads(cache_file.read_text(encoding="utf-8"))
                log.info("Loaded cached transcript from %s", cache_file)
                return FullTranscript.from_dict(data)
            except Exception as exc:
                log.warning("Failed to load transcript cache from %s: %s", cache_file, exc)

        model = self._load_model()
        log.info("Starting transcription of %s...", audio_path)

        import numpy as np
        import subprocess
        from .utils import get_ffmpeg_binary
        ffmpeg_bin = get_ffmpeg_binary()
        proc = subprocess.Popen(
            [ffmpeg_bin, "-v", "error", "-i", str(audio_path), "-f", "f32le", "-ac", "1", "-ar", "16000", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        raw_pcm, _ = proc.communicate()
        audio_data = np.frombuffer(raw_pcm, np.float32)

        # Transcribe with word timestamps enabled using loaded PCM float32 array
        # beam_size=1 provides 2.5x-3x speedup on CPU without quality loss for conversational audio
        # condition_on_previous_text=False prevents repetitive hallucination loops and temperature fallback delays
        segments_gen, info = model.transcribe(
            audio_data,
            beam_size=1,
            word_timestamps=True,
            vad_filter=True,
            vad_parameters=dict(min_silence_duration_ms=500),
            condition_on_previous_text=False,
            initial_prompt="Transcribe strictly in English and Romanized Hinglish using the Latin alphabet. Never output Urdu, Arabic, or foreign scripts.",
        )

        segments: List[TranscriptSegment] = []
        for i, seg in enumerate(segments_gen):
            if i % 5 == 0 or seg.end >= getattr(info, "duration", 0.0) - 5:
                log.info("Transcribed up to %.1fs / %.1fs (segment %d)", seg.end, getattr(info, "duration", 0.0), i + 1)
            words = []
            if seg.words:
                for w in seg.words:
                    cleaned_w = clean_to_english_hinglish(w.word.strip(), keep_emojis=False) or w.word.strip()
                    words.append(
                        WordTimestamp(
                            word=cleaned_w,
                            start=round(w.start, 2),
                            end=round(w.end, 2),
                            probability=round(w.probability, 3),
                        )
                    )
            cleaned_text = clean_to_english_hinglish(seg.text.strip(), keep_emojis=False) or seg.text.strip()
            segments.append(
                TranscriptSegment(
                    id=i,
                    start=round(seg.start, 2),
                    end=round(seg.end, 2),
                    text=cleaned_text,
                    words=words,
                )
            )

        duration = getattr(info, "duration", 0.0)
        language = getattr(info, "language", "en")
        transcript = FullTranscript(
            episode_id=episode_id,
            duration_seconds=round(duration, 2),
            language=language,
            segments=segments,
        )

        if cache_file:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(
                json.dumps(transcript.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.info("Saved transcript cache to %s (%d segments)", cache_file, len(segments))

        return transcript
