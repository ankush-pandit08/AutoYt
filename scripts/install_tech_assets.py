"""Restore the reviewed local tech library without redistributing stock binaries."""
import json
from pathlib import Path

from autoyt.audio import probe
from autoyt.config import Settings
from autoyt.licensing import audit
from autoyt.net import Web
from autoyt.store import digest


def main():
    folder = Path(__file__).resolve().parents[1] / "assets" / "footage"
    items = json.loads((folder / "tech-library.catalog.json").read_text(encoding="utf-8"))
    audit(items)
    cfg = Settings()
    for item in items:
        path = folder / item["file"]
        if path.parent.resolve() != folder.resolve():
            raise ValueError("Asset path must stay inside the library")
        if path.is_file() and digest(path) == item["sha256"]:
            continue
        temporary = path.with_suffix(path.suffix + ".part")
        try:
            Web().download(item["media_url"], temporary, max_bytes=80_000_000)
            if digest(temporary) != item["sha256"]:
                raise ValueError("Source changed: manually review before approving this asset")
            if item["kind"] == "stock":
                probe(cfg, temporary)
            else:
                from PIL import Image
                with Image.open(temporary) as image:
                    image.verify()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        print("Installed", path.name)
    manifest = folder / "manifest.json"
    if manifest.exists():
        backup = folder / "manifest.json.backup"
        if not backup.exists():
            backup.write_bytes(manifest.read_bytes())
    manifest.write_text(json.dumps(items, indent=2) + "\n", encoding="utf-8")
    print(f"Ready: {len(items)} reviewed technology assets")


if __name__ == "__main__":
    main()
