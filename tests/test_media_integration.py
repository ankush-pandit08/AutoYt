import shutil
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from PIL import Image

from autoyt import video as video_module
from autoyt.video import quality, render, subtitles


@pytest.mark.integration
@pytest.mark.parametrize("asset_kind", ["graphics", "stock_image"])
def test_actual_ffmpeg_render_and_decode(cfg, script, tmp_path, monkeypatch, asset_kind):
    root = Path(__file__).resolve().parents[1]
    cfg.ffmpeg = shutil.which("ffmpeg") or str(root/"data/tools/ffmpeg.exe")
    cfg.ffprobe = shutil.which("ffprobe") or str(root/"data/tools/ffprobe.exe")
    if not Path(cfg.ffmpeg).is_file() or not Path(cfg.ffprobe).is_file():
        pytest.skip("Install FFmpeg and FFprobe for the real media integration test")
    cfg.video_width, cfg.video_height = 180, 320
    cfg.video_duration_seconds, cfg.duration_min, cfg.duration_max = 2, .9, 2.2
    cfg.encoder_preset = "ultrafast"
    displayed_context = []
    original_card = video_module.card

    def checked_card(settings, headline, context, *args, **kwargs):
        displayed_context.append(context)
        return original_card(settings, headline, context, *args, **kwargs)

    monkeypatch.setattr(video_module, "card", checked_card)
    rate = 24000
    sf.write(tmp_path/"narration.wav", .2*np.sin(np.arange(rate)*2*np.pi*220/rate), rate)
    segments = [{"text": s.narration, "start": i/6, "end": (i+1)/6} for i, s in enumerate(script.scenes)]
    image = tmp_path / "licensed-photo.jpg"
    if asset_kind == "stock_image":
        Image.new("RGB", (180, 320), (25, 70, 100)).save(image)
    assets = [{"kind": asset_kind, "path": str(image) if asset_kind == "stock_image" else None}
              for _ in script.scenes]
    timing = subtitles(cfg, segments, tmp_path)
    final = render(cfg, script, segments, assets, tmp_path)
    assert final.is_file() and final.stat().st_size > 1000
    report = quality(cfg, final, timing, tmp_path/"narration.wav")
    assert report["passed"]
    assert .9 <= report["duration"] <= 1.2  # Natural voice duration; no padding to the 2s target.
    assert displayed_context and all(t == script.title for t in displayed_context)
    cfg.video_width, cfg.video_height = 360, 640
    with pytest.raises(ValueError, match="dimensions"):
        quality(cfg, final, timing, tmp_path/"narration.wav")
