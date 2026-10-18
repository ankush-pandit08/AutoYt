"""Download the pinned, public IndicF5 Hinglish model and Vocos assets."""
import hashlib
import json
from pathlib import Path

from autoyt.net import Web

MODEL = "Tharshan/indicf5_hindi-english_code_switch"
REVISION = "fb6c08203e1f159960a75c7c70bd46a8fe25d5b9"
VOCODER = "charactr/vocos-mel-24khz"
VOCODER_REVISION = "0feb3fdd929bcd6649e0e7c5a688cf7dd012ef21"
MODEL_HASH = "de6350f93982ebd9764fb20bd327e41eb1c5ab2a068e00d6de4392891ac488b7"


def main():
    root = Path("data/models/indicf5")
    web = Web()
    manifest = {"model": MODEL, "revision": REVISION, "license": "Apache-2.0", "files": {}}
    assets = [(name, MODEL, REVISION, name) for name in (
        "README.md", "model.py", "config.json", "cfm.py", "dit.py", "modules.py", "utils.py",
        "vocab.txt", "model.safetensors")]
    assets += [("vocoder/" + name, VOCODER, VOCODER_REVISION, name)
               for name in ("config.yaml", "pytorch_model.bin")]
    for local, repo, revision, remote in assets:
        path = root / local
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file():
            print("Downloading", local, flush=True)
            temporary = path.with_suffix(path.suffix + ".part")
            web.download(f"https://huggingface.co/{repo}/resolve/{revision}/{remote}", temporary,
                         max_bytes=1_500_000_000)
            temporary.replace(path)
        with path.open("rb") as stream:
            sha = hashlib.file_digest(stream, "sha256").hexdigest()
        if local == "model.safetensors" and sha != MODEL_HASH:
            raise ValueError("IndicF5 model checksum mismatch")
        manifest["files"][local] = sha
        print("Verified", local, flush=True)
    (root / "provenance.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print("IndicF5 local model is ready. Configure a reference WAV and its exact transcript.", flush=True)


if __name__ == "__main__":
    main()
