import base64
import copy
import json
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from autoyt import creative, licensing, render_settings


def animation():
    return json.loads((creative.BUILTIN / "tech-pulse.json").read_text())


def request(data=None, **updates):
    return {"kind": "animation", "name": "Tech pulse", "author": "Test creator",
            "source_url": "https://lottiefiles.com/free-animation/test-pulse",
            "tags": ["coding", "cloud"], "license": "Lottie Simple License", "rights_confirmed": True,
            "data": base64.b64encode(json.dumps(data or animation()).encode()).decode(), **updates}


def test_import_selection_and_license_audit(cfg, tmp_path):
    cfg.creative_manifest = tmp_path / "library" / "manifest.json"
    result = creative.import_asset(cfg, request())
    assert result["creative_library"]["animations"] == 1
    records = creative.catalog(cfg)
    assert licensing.audit(records)["assets"][0]["provider"] == "LottieFiles"
    selected, credit = creative.select_animation(cfg, "new cloud coding tools", records)
    assert selected == animation() and credit["source_url"].startswith("https://lottiefiles.com/")
    _, original = creative.select_animation(cfg, "smartphone battery", records)
    assert original["license"] == "Original AutoYT graphics"
    creative.import_asset(cfg, request())
    assert len(creative.catalog(cfg)) == 1  # Same bytes never duplicate the library.
    Path(records[0]["path"]).write_text('{}')
    assert not creative.catalog(cfg)  # Tampered content cannot be rendered.


@pytest.mark.parametrize("patch", [
    {"source_url": "http://lottiefiles.com/file"},
    {"source_url": "https://lottiefiles.com.evil.example/file"},
    {"source_url": "https://user:secret@lottiefiles.com/file"},
    {"source_url": "https://lottiefiles.com/file?token=secret"},
    {"license": "CC-BY-4.0"}, {"rights_confirmed": False},
    {"data": "invalid-base64"}, {"tags": []}, {"author": ""},
])
def test_import_rejects_unlicensed_or_invalid_content(cfg, tmp_path, patch):
    cfg.creative_manifest = tmp_path / "library" / "manifest.json"
    with pytest.raises(ValueError):
        creative.import_asset(cfg, request(**patch))
    assert not cfg.creative_manifest.is_file()


@pytest.mark.parametrize("mutation", ["expression", "image", "font", "external", "duration", "nan", "deep", "repeater"])
def test_lottie_rejects_active_external_or_unbounded_content(mutation):
    data = copy.deepcopy(animation())
    if mutation == "expression":
        data["layers"][0]["ks"]["p"]["x"] = "fetch('https://example.com')"
    elif mutation == "image":
        data["layers"][0]["ty"] = 2
    elif mutation == "font":
        data["fonts"] = {"list": []}
    elif mutation == "external":
        data["assets"] = [{"u": "https://example.com/", "p": "secret.png"}]
    elif mutation == "duration":
        data["op"] = 100000
    elif mutation == "nan":
        data["fr"] = float("nan")
    elif mutation == "repeater":
        data["layers"][0]["shapes"].append({"ty": "rp", "c": {"k": 10000000}})
    else:
        nested = data
        for _ in range(50):
            nested["nested"] = {}
            nested = nested["nested"]
    with pytest.raises(ValueError):
        creative.validate_lottie(data)


def test_catalog_rejects_path_escape(cfg, tmp_path):
    cfg.creative_manifest = tmp_path / "library" / "manifest.json"
    creative.import_asset(cfg, request())
    entries = json.loads(cfg.creative_manifest.read_text())
    entries[0]["file"] = "../../outside.json"
    cfg.creative_manifest.write_text(json.dumps(entries))
    assert not creative.catalog(cfg)


def test_sound_bus_is_quiet_bounded_faded_and_optional(cfg, tmp_path):
    segments = [{"text": "AI model update.", "start": 0, "end": 2},
                {"text": "Coding tools.", "start": 2, "end": 4}]
    cfg.sound_effects = True
    path, records = creative.sound_bus(cfg, segments, tmp_path, [])
    samples, rate = sf.read(path)
    assert len(samples) == 4*48000 and rate == 48000
    assert np.max(np.abs(samples)) <= .101 and np.isfinite(samples).all()
    assert abs(samples[0]) < .001 and np.max(np.abs(samples[rate:rate*2])) == 0
    assert len(records) == 2 and licensing.audit(records)["passed"]
    cfg.sound_effects = False
    assert creative.sound_bus(cfg, segments, tmp_path, []) == (None, [])


def test_local_cc0_sound_selected_by_tags(cfg, tmp_path):
    path = tmp_path / "sample.wav"
    sf.write(path, np.sin(np.arange(4800)*.1)*.4, 48000)
    credit = {"kind": "sound_effect", "path": str(path), "tags": ["coding"],
              "source_url": "https://freesound.org/people/test/sounds/1/", "license": "CC0-1.0",
              "license_url": creative.CC0, "attribution": "Test creator", "free_to_use": True,
              "permitted_use": True}
    cfg.sound_effects = True
    _, records = creative.sound_bus(cfg, [{"start": 0, "end": 1, "text": "coding"}], tmp_path, [credit])
    assert records[0]["source_url"] == credit["source_url"]
    assert licensing.audit(records)["assets"][0]["provider"] == "Freesound CC0"
    with pytest.raises(ValueError):
        licensing.audit([{**credit, "license": "CC-BY-NC-4.0"}])


def test_enhancement_settings_round_trip(cfg):
    updated, saved = render_settings.save(cfg, {"render_mode": "hybrid", "render_fallback": True,
        "motion_icons": False, "motion_lottie": True, "sound_effects": False}, 0)
    assert not updated.motion_icons and not updated.sound_effects and updated.motion_lottie
    assert render_settings.apply(cfg).motion_icons is False
    assert saved["selection"]["sound_effects"] is False
    with pytest.raises(ValueError):
        render_settings.RenderSelection(sound_effects="false")


def test_topic_icons_match_words_without_substring_false_matches():
    assert creative.topic_icon("OpenAI AI model") == {"name": "brain-circuit", "label": "AI & MODELS"}
    assert creative.topic_icon("coding tools")["name"] == "code-xml"
    assert creative.topic_icon("train maintenance")["name"] == "network"  # 'ai' inside train is not AI.


def test_audio_import_normalizes_without_network_protocols(cfg, tmp_path, monkeypatch):
    cfg.creative_manifest = tmp_path / "library" / "manifest.json"
    def convert(command, **kwargs):
        assert command[command.index("-protocol_whitelist")+1] == "file,pipe"
        assert command[command.index("-format_whitelist")+1] == "wav,mp3,ogg"
        sf.write(command[-1], np.sin(np.arange(4800)*.1)*.4, 48000)
    monkeypatch.setattr("autoyt.audio.run", convert)
    result = creative.import_asset(cfg, request(kind="sound_effect", license="CC0-1.0",
        source_url="https://freesound.org/people/test/sounds/1/", data=base64.b64encode(b"audio input").decode()))
    assert result["creative_library"]["sounds"] == 1
    assert creative.catalog(cfg)[0]["path"].endswith(".wav")


def test_http_import_requires_csrf_and_does_not_start_video(cfg):
    import threading

    import httpx

    from autoyt.dashboard import Dashboard, make_server

    app = Dashboard(cfg)
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        with httpx.Client(base_url=origin, trust_env=False) as client:
            assert client.post("/api/creative/import", json=request()).status_code == 403
            response = client.post("/api/creative/import", json=request(), headers={
                "Origin": origin, "X-AutoYT-Token": app.token})
            assert response.status_code == 200
            assert response.json()["creative_library"]["animations"] == 1
            assert app.future is None
            app.future = type("Active", (), {"done": lambda self: False})()
            blocked = client.post("/api/creative/import", json=request(), headers={
                "Origin": origin, "X-AutoYT-Token": app.token})
            assert blocked.status_code == 409
            app.future = None
    finally:
        server.shutdown()
        server.server_close()
        app.close()
