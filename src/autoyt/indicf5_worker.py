"""Isolated local inference worker; receives data through a JSON file, never shell text."""
import hashlib
import importlib.util
import json
import sys
import time
from pathlib import Path


def load_model(root, threads=2):
    import torch
    from safetensors.torch import load_file
    from vocos import Vocos

    torch.set_num_threads(threads)
    torch.set_num_interop_threads(1)
    root = Path(root)
    manifest = json.loads((root / "provenance.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        with (root / name).open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != expected:
                raise ValueError("Local IndicF5 model file changed: " + name)
    spec = importlib.util.spec_from_file_location(
        "local_indicf5", root / "model.py", submodule_search_locations=[str(root)])
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    architecture = importlib.import_module(spec.name + ".modules")
    print("Loading local IndicF5 Hinglish model on CPU", flush=True)
    config = module.IndicF5HinglishConfig(**json.loads(
        (root / "config.json").read_text(encoding="utf-8"))["model_config"])
    # Allocate the architecture without a second 1.35 GB random-weight copy.
    with torch.device("meta"):
        model = module.IndicF5Hinglish(config, vocab_path=str(root / "vocab.txt"), asset_dir=str(root))
    weights = load_file(root / "model.safetensors", device="cpu")
    _, weights = module._strip_prefix(weights, set(model.model.state_dict()))
    missing, unexpected = model.model.load_state_dict(weights, strict=False, assign=True)
    if missing or unexpected:
        raise ValueError("IndicF5 checkpoint does not match the pinned architecture")
    del weights
    # These deterministic buffers are intentionally absent from the checkpoint.
    for layer in model.modules():
        for name, buffer in list(layer.named_buffers(recurse=False)):
            if buffer.is_meta:
                if name == "freqs_cis":
                    layer.register_buffer(name, architecture.precompute_freqs_cis(
                        buffer.shape[-1], buffer.shape[0]), persistent=False)
                elif name == "dummy":
                    layer.register_buffer(name, torch.tensor(0), persistent=False)
                else:
                    raise ValueError("Unexpected uninitialized IndicF5 buffer: " + name)
    model.eval()
    vocoder = Vocos.from_hparams(str(root / "vocoder/config.yaml"))
    weights = torch.load(root / "vocoder/pytorch_model.bin", map_location="cpu", weights_only=True)
    vocoder.load_state_dict(weights)
    del weights
    model._vocoder = vocoder.eval()
    return model


def main():
    import numpy as np
    import soundfile as sf
    from indicf5_optimizations import cached_conditioning

    started = time.monotonic()
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    model = load_model(request["model_dir"], request.get("threads", 2))
    load_seconds = time.monotonic() - started
    report = {"load_seconds": round(load_seconds, 3), "segments": [],
              "threads": request.get("threads", 2), "steps": request["steps"],
              "speech_speed": request["speed"], "precision": "float32"}
    for item in request["items"]:
        segment_started = time.monotonic()
        print("Generating IndicF5 segment", item["index"], flush=True)
        with cached_conditioning(model):
            samples, rate = model.generate(
                item["text"], ref_audio=request["reference_audio"], ref_text=request["reference_text"],
                nfe_step=request["steps"], speed=request["speed"], cfg_strength=2.0)
        if not len(samples) or not np.isfinite(samples).all():
            raise ValueError("IndicF5 produced invalid audio")
        target = Path(item["output"])
        temporary = target.with_suffix(".tmp.wav")
        sf.write(temporary, samples, rate)
        temporary.replace(target)
        elapsed = time.monotonic() - segment_started
        report["segments"].append({"index": item["index"], "seconds": round(elapsed, 3),
                                   "audio_seconds": len(samples) / rate})
        print(f"Segment {item['index']} generated in {elapsed:.1f}s", flush=True)
    report["total_seconds"] = round(time.monotonic() - started, 3)
    if request.get("metrics_path"):
        Path(request["metrics_path"]).write_text(json.dumps(report, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
