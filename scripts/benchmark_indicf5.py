"""Benchmark local inference threads and exact conditioning reuse; never creates a video."""
import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--render", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src/autoyt"))
    os.environ.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
    import soundfile as sf
    import torch
    import torch.nn.functional as functional
    from indicf5_optimizations import cached_conditioning
    from indicf5_worker import load_model

    started = time.monotonic()
    model = load_model(root / "data/models/indicf5", threads=2)
    report = {"load_seconds": round(time.monotonic() - started, 3), "precision": "float32",
              "steps": 16, "cfg_strength": 2.0, "threads": []}
    ref_audio = str(root / "data/voices/indicf5-reference.wav")
    ref_text = (root / "data/voices/indicf5-reference.txt").read_text(encoding="utf-8").strip()
    text = "आज AI tools तेजी से बदल रहे हैं। अपनी online privacy का ध्यान रखना जरूरी है।"
    with torch.inference_mode():
        ref, _ = model._load_ref(ref_audio)
        cond = model.model.mel_spec(ref).permute(0, 2, 1)
        ref_len = ref.shape[-1] // 256
        frames = ref_len + int(ref_len / len(ref_text.encode()) * len(text.encode()))
        cond = functional.pad(cond, (0, 0, 0, frames - cond.shape[1]))
        tokenize = sys.modules["local_indicf5.utils"].list_str_to_idx
        tokens = tokenize([ref_text + text], model.model.vocab_char_map)
        torch.manual_seed(77)
        x = torch.randn(1, frames, 100)
        timestep = torch.tensor(.5)

        def forward():
            return model.model.transformer(x=x, cond=cond, text=tokens, time=timestep,
                drop_audio_cond=False, drop_text=False, mask=None)

        baseline = forward().clone()
        with cached_conditioning(model):
            assert torch.equal(baseline, forward()), "Conditioning cache changed numerical output"
            assert torch.equal(baseline, forward()), "Reused conditioning changed numerical output"
        report["conditioning_cache_bitwise_equal"] = True
        for threads in (2, 4, 6):
            torch.set_num_threads(threads)
            times = []
            with cached_conditioning(model):
                forward()  # Warm the thread pool and conditioning caches.
                for _ in range(2):
                    tick = time.monotonic()
                    output = forward()
                    times.append(time.monotonic() - tick)
            difference = float((baseline - output).abs().max())
            torch.testing.assert_close(baseline, output, atol=1e-4, rtol=1e-4)
            result = {"threads": threads, "forward_seconds": round(statistics.median(times), 4),
                      "max_abs_difference": difference}
            report["threads"].append(result)
            print(json.dumps(result), flush=True)
        best = min(report["threads"], key=lambda row: row["forward_seconds"])["threads"]
        report["selected_threads"] = best
        if args.render:
            torch.set_num_threads(best)
            torch.manual_seed(77)
            tick = time.monotonic()
            with cached_conditioning(model):
                samples, rate = model.generate(text, ref_audio=ref_audio, ref_text=ref_text,
                                               nfe_step=16, cfg_strength=2.0, speed=1.0)
            report["generation_seconds"] = round(time.monotonic() - tick, 3)
            report["audio_seconds"] = len(samples) / rate
            sf.write(root / "output/voice-previews/indicf5-optimized.wav", samples, rate)
    path = root / "output/voice-previews/indicf5-benchmark.json"
    path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
