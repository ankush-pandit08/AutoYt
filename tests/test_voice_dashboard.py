import json
import os
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import numpy as np
import pytest
import soundfile as sf

from autoyt.dashboard import Dashboard, make_server


def test_preview_does_not_save_throttles_and_protects_sample(cfg, monkeypatch):
    cfg.gemini_api_key = "private-gemini-key"

    def segment(self, text, path):
        sf.write(path, 0.2 * np.sin(np.arange(24000) * 0.1), 24000)

    monkeypatch.setattr("autoyt.audio.Speech.segment", segment)
    app = Dashboard(cfg)
    app.start()
    server = make_server(app, 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    value = {"provider": "gemini", "voice": "Puck", "language": "en"}
    try:
        body = json.dumps({"action": "test_voice", "selection": value}).encode()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(base + "/api/action", data=body))
        assert error.value.code == 403
        with urlopen(
            Request(base + "/api/action", data=body, headers={"Origin": base, "X-AutoYT-Token": app.token})
        ) as response:
            ident = json.load(response)["preview_id"]
        app.voice_future.result(timeout=5)
        assert app.voice_result["state"] == "READY"
        assert not (cfg.database_path.parent / "voice-settings.json").exists()
        assert not app.store.jobs()
        with urlopen(base + f"/api/voice-preview/{ident}.wav") as response:
            assert response.headers["Content-Type"] == "audio/wav"
            assert response.read().startswith(b"RIFF")
        with pytest.raises(ValueError, match="30 seconds"):
            app.voice_action("test_voice", value)
        file = cfg.cache_dir / "voice-previews" / (ident + ".wav")
        os.utime(file, (0, 0))
        with pytest.raises(HTTPError) as error:
            urlopen(base + f"/api/voice-preview/{ident}.wav")
        assert error.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
        app.close()


def test_missing_key_preview_reports_safe_error_and_save_stops(cfg):
    app = Dashboard(cfg)
    app.start()
    value = {"provider": "elevenlabs", "voice": "voice_from_api", "language": "hi"}
    try:
        with pytest.raises(ValueError, match="ELEVENLABS_API_KEY"):
            app.voice_action("save_voice", value, 0)
        app.voice_action("test_voice", value)
        app.voice_future.result(timeout=5)
        assert app.voice_result["state"] == "FAILED"
        assert "ELEVENLABS_API_KEY" in app.voice_result["error"]
        assert app.cfg.tts_engine == "kokoro"
        assert not app.store.jobs()
    finally:
        app.close()
