"""Generate a local Hinglish voice preview without changing scheduled uploads."""
import argparse
from pathlib import Path

from autoyt.audio import Speech, check_audio
from autoyt.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/voice-previews/gemini-puck-hinglish.wav"))
    args = parser.parse_args()
    cfg = Settings().model_copy(update={"tts_engine": "gemini", "tts_voice": "Puck"})
    text = (
        "नमस्ते दोस्तों! TechGyaan में आपका स्वागत है। "
        "आज बात करते हैं AI, smartphone updates और आपकी online privacy की। "
        "नई technology को समझना मुश्किल नहीं होना चाहिए। "
        "हम आपको बताएंगे सीधी बात, आसान भाषा में, बिना किसी hype के।")
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        Speech(cfg).segment(text, args.output)
        stats = check_audio(args.output)
    except (RuntimeError, ValueError):
        raise SystemExit("Gemini voice preview failed; check API access and quotas. Settings unchanged.") from None
    print(f"Preview: {args.output.resolve()}")
    print(f"Duration: {stats['duration']:.1f}s; audio checks passed")


if __name__ == "__main__":
    main()
