"""Generate local Hindi male voice previews; never creates a job or uploads."""
from pathlib import Path

from autoyt.audio import Speech, check_audio
from autoyt.config import Settings
from autoyt.store import write_json


def main():
    cfg = Settings()
    cfg.tts_engine, cfg.narration_language = "kokoro", "hi"
    folder = Path("output/previews/hindi")
    folder.mkdir(parents=True, exist_ok=True)
    text = ("नमस्कार! आप देख रहे हैं टेक ज्ञान। यहाँ हम नई तकनीक को आसान हिंदी में समझते हैं। "
            "कोई भी बड़ी खबर साझा करने से पहले, उसके स्रोत और तथ्यों की जाँच ज़रूरी है। "
            "चलिए, तकनीक की दुनिया को थोड़ा बेहतर समझते हैं।")
    (folder / "sample.txt").write_text(text, encoding="utf-8")
    speech = Speech(cfg)
    report = {}
    for voice in ("hm_omega", "hm_psi"):
        cfg.tts_voice = voice
        path = folder / (voice + ".wav")
        speech.segment(text, path)
        report[voice] = check_audio(path)
        print(voice, report[voice], flush=True)
    write_json(folder / "voice_check.json", {"language": "hi", "speed": cfg.tts_speed, "voices": report})


if __name__ == "__main__":
    main()
