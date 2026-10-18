"""Generate and validate a local Hinglish sample without creating or uploading a video."""
import argparse
import logging
import time
from pathlib import Path

from autoyt.audio import Speech, check_audio
from autoyt.config import Settings
from autoyt.store import write_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/voice-previews/indicf5-hinglish.wav"))
    args = parser.parse_args()
    cfg = Settings().model_copy(update={"tts_engine": "indicf5", "tts_voice": "male_reference"})
    text = "आज AI tools तेजी से बदल रहे हैं। अपनी online privacy का ध्यान रखना जरूरी है।"
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    started = time.monotonic()
    Speech(cfg).segment(text, args.output)
    stats = check_audio(args.output)
    stats["elapsed_seconds"] = round(time.monotonic() - started, 2)
    write_json(args.output.with_suffix(".json"), stats)
    print(f"Preview: {args.output.resolve()}")
    print(f"Duration: {stats['duration']:.1f}s; generated in {stats['elapsed_seconds']}s; audio checks passed")


if __name__ == "__main__":
    main()
