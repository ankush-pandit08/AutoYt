from pathlib import Path

from PIL import Image

from autoyt.media import choose_media, local_queries
from autoyt.store import write_json


def entry(file, **extra):
    return {"file": file, "kind": "stock", "source_url": "https://www.pexels.com/video/"+file,
            "license": "Pexels License https://www.pexels.com/license/", "attribution": "Test creator",
            "permitted_use": "Edited technology explainer", "tags": ["person", "computer", "coding"], **extra}


def test_local_footage_precedes_images_and_is_not_repeated(cfg, script, tmp_path, monkeypatch):
    cfg.local_media_manifest = tmp_path / "manifest.json"
    (tmp_path / "code.mp4").write_bytes(b"video")
    Image.new("RGB", (800, 900), "teal").save(tmp_path / "photo.jpg")
    write_json(cfg.local_media_manifest, [entry("photo.jpg", kind="stock_image"), entry("code.mp4")])
    for scene in script.scenes:
        scene.visual_query = "AI assistant context"
    monkeypatch.setattr("autoyt.media.probe", lambda *args: {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]})
    assets = choose_media(cfg, None, script)
    assert assets[0]["kind"] == "stock" and assets[1]["kind"] == "stock_image"
    assert all(a["kind"] == "graphics" for a in assets[2:])
    assert Path(assets[0]["path"]).name == "code.mp4"


def test_local_ai_expansion_does_not_replace_an_unrelated_subject(script):
    scene = script.scenes[0].model_copy(update={"visual_query": "phone battery", "narration": "A phone battery stores energy."})
    assert local_queries(scene) == ["phone battery"]
