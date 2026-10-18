"""Generate a local Hinglish voice preview without changing scheduled uploads."""
import argparse
import base64
import io
from pathlib import Path

import httpx
import soundfile as sf

from autoyt.audio import check_audio
from autoyt.config import Settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--text-file", type=Path)
    parser.add_argument("--output", type=Path, default=Path("output/voice-previews/gemini-puck-hinglish.wav"))
    args = parser.parse_args()
    cfg = Settings()
    if not cfg.gemini_api_key:
        raise SystemExit("Save GEMINI_API_KEY in .env first.")
    text = (
        "नमस्ते दोस्तों! TechGyaan में आपका स्वागत है। "
        "आज बात करते हैं AI, smartphone updates और आपकी online privacy की। "
        "नई technology को समझना मुश्किल नहीं होना चाहिए। "
        "हम आपको बताएंगे सीधी बात, आसान भाषा में, बिना किसी hype के।")
    if args.text_file:
        text = args.text_file.read_text(encoding="utf-8")
    body = {
        "model": "gemini-3.8-flash-tts",
        "input": [{"type": "user_input", "content": [{"type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style":
                "Natural Indian male technology presenter speaking conversational Hinglish. "
                "Warm, clear, energetic but relaxed. Natural Indian English pronunciation of English "
                "tech words with smooth Hindi transitions. Moderate pace, brief natural pauses, "
                "no shouting or exaggerated announcer delivery."}]}]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": "Puck"}]},
    }
    try:
        response = httpx.post("https://generativelanguage.googleapis.com/v1beta/interactions",
                              headers={"x-goog-api-key": cfg.gemini_api_key}, json=body, timeout=180)
        response.raise_for_status()
        blocks = [part for step in response.json().get("steps", []) if step.get("type") == "model_output"
                  for part in step.get("content", []) if part.get("type") == "audio"]
        raw = base64.b64decode(blocks[-1]["data"], validate=True)
        samples, rate = sf.read(io.BytesIO(raw))
        path = args.output
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, samples, rate)
        stats = check_audio(path)
        print(f"Preview: {path.resolve()}")
        print(f"Duration: {stats['duration']:.1f}s; audio checks passed")
    except httpx.HTTPStatusError as error:
        raise SystemExit(f"Gemini TTS returned HTTP {error.response.status_code}; "
                         "check model access and free-tier quotas in AI Studio.") from None
    except (httpx.TransportError, ValueError, KeyError, IndexError, RuntimeError):
        raise SystemExit("Gemini voice preview failed; no automatic voice settings changed.") from None


if __name__ == "__main__":
    main()
