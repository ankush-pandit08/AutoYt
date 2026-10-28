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
    assert local_queries(scene)[0] == "phone battery"
    assert "computer programming" not in local_queries(scene)


def test_face_free_only_uses_reviewed_assets_and_never_downloads_unreviewed_stock(cfg, script, tmp_path, monkeypatch):
    cfg.face_free_media = True
    cfg.local_media_manifest = tmp_path / "manifest.json"
    cfg.pexels_api_key = cfg.pixabay_api_key = "test-key"
    cfg.enable_public_images = True
    for name in ('unknown.mp4', 'face.mp4', 'reviewed.mp4'):
        (tmp_path/name).write_bytes(b'video')
    write_json(cfg.local_media_manifest, [entry('unknown.mp4'), entry('face.mp4',face_free=False),
                                         entry('reviewed.mp4',face_free=True)])
    for scene in script.scenes:
        scene.visual_query = 'computer coding'
    monkeypatch.setattr('autoyt.media.probe', lambda *args: {'streams':[{'codec_type':'video','width':1920,'height':1080}]})
    class Offline:
        def get(self, *args, **kwargs):
            raise AssertionError('Unreviewed online stock must not be used in face-free mode')
    assets = choose_media(cfg, Offline(), script)
    assert Path(assets[0]['path']).name == 'reviewed.mp4'
    assert all(a['kind']=='graphics' for a in assets[1:])


def test_specific_subject_photo_beats_generic_ai_video(cfg, script, tmp_path, monkeypatch):
    cfg.local_media_manifest = tmp_path/'manifest.json'
    (tmp_path/'generic.mp4').write_bytes(b'video')
    Image.new('RGB',(1600,1000),'teal').save(tmp_path/'gpu.jpg')
    write_json(cfg.local_media_manifest,[entry('generic.mp4',tags=['computer','programming','keyboard']),
        entry('gpu.jpg',kind='stock_image',tags=['gpu','graphics','card','hardware'])])
    script.scenes=script.scenes[:1]
    script.scenes[0].visual_query='gpu graphics card'
    script.scenes[0].narration='This AI workload uses a graphics card.'
    monkeypatch.setattr('autoyt.media.probe',lambda *args:{'streams':[{'codec_type':'video','width':1920,'height':1080}]})
    assert choose_media(cfg,None,script)[0]['kind']=='stock_image'
