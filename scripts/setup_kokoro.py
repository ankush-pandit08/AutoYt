"""Download pinned Kokoro CPU model/voices; verify release SHA-256 before use."""
from autoyt.config import Settings
from autoyt.net import Web
from autoyt.store import digest, write_json

RELEASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
FILES = {
    "kokoro-v1.0.int8.onnx": "ae315a79b623f244700e4afb9246c46a26066782e049ba174bf3ba433970ee9c",
    "voices-v1.0.bin": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
}


def main():
    cfg = Settings()
    web = Web()
    for path in (cfg.kokoro_model, cfg.kokoro_voices):
        expected = FILES.get(path.name)
        if not expected:
            raise ValueError("Setup supports only the pinned filenames; configure custom models manually")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.is_file() or digest(path) != expected:
            temporary = path.with_suffix(path.suffix + ".part")
            print("Downloading", path.name, flush=True)
            web.download(RELEASE + path.name + "?download=1", temporary, max_bytes=400_000_000)
            if digest(temporary) != expected:
                raise ValueError("Model checksum mismatch; incomplete file will not be used")
            temporary.replace(path)
        print("Verified", path.name, flush=True)
    write_json(cfg.kokoro_model.parent / "provenance.json", {
        "source": RELEASE, "sha256": FILES, "model_license": "Apache-2.0",
        "model_card": "https://huggingface.co/hexgrad/Kokoro-82M",
        "runtime_license": "MIT", "runtime": "https://github.com/thewh1teagle/kokoro-onnx"})
    font_dir = cfg.cache_dir.parent / "fonts"
    font_dir.mkdir(parents=True, exist_ok=True)
    font_source = "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansdevanagari/"
    for remote, local in (("NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf", "NotoSansDevanagari.ttf"),
                          ("OFL.txt", "OFL.txt")):
        path = font_dir / local
        if not path.is_file():
            web.download(font_source + remote, path, max_bytes=5_000_000)
    print("Hindi font and its OFL license are available locally", flush=True)


if __name__ == "__main__":
    main()
