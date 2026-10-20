"""Small common interface; existing Gemini implementation is delegated unchanged."""

from pathlib import Path
from typing import Protocol


class TTSProvider(Protocol):
    def segment(self, cfg, text: str, path: Path) -> None: ...


def provider(engine: str) -> TTSProvider:
    if engine == "gemini":
        from . import gemini_speech

        return gemini_speech
    if engine == "elevenlabs":
        from . import elevenlabs_speech

        return elevenlabs_speech
    raise ValueError("Unsupported hosted voice provider")
