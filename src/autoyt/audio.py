import json
import platform
import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def hindi_spoken_text(text):
    # Pronunciation-only aliases; the original script, captions and source package IDs stay exact.
    aliases = {"SafeDep": "सेफ़डेप", "imgdecode": "आईएमजी डिकोड", "npm": "एनपीएम",
               "Windows": "विंडोज़", "JavaScript": "जावास्क्रिप्ट", "DLL": "डी एल एल",
               "GitHub": "गिटहब", "console": "कंसोल", "Node.js": "नोड जेएस"}
    for name in sorted(aliases, key=len, reverse=True):
        text = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", aliases[name], text, flags=re.IGNORECASE)
    return text


def run(command, *, cwd=None, timeout=900, input=None):
    result = subprocess.run([str(x) for x in command], cwd=cwd, input=input, capture_output=True,
                            text=True, timeout=timeout, encoding="utf-8", errors="replace")
    if result.returncode:
        raise RuntimeError("Local tool failed: " + Path(str(command[0])).name + "\n" + result.stderr[-1600:])
    return result


def probe(cfg, path):
    result = run([cfg.ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
    return json.loads(result.stdout)


def check_audio(path):
    data, rate = sf.read(path)
    if not len(data) or not np.isfinite(data).all():
        raise ValueError("Invalid narration audio")
    peak = float(np.max(np.abs(data)))
    rms = float(np.sqrt(np.mean(data ** 2)))
    clipping = float(np.mean(np.abs(data) >= .999))
    if rms < .0005 or peak < .005:
        raise ValueError("Narration is silent")
    if clipping > .005:
        raise ValueError("Narration has excessive clipping")
    return {"duration": len(data)/rate, "peak": peak, "rms": rms, "clipping_fraction": clipping}


class Speech:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pipeline = None

    def segment(self, text, path):
        if self.cfg.tts_engine == "indicf5":
            from .indicf5_speech import segment
            segment(self.cfg, text, path)
        elif self.cfg.tts_engine in {"gemini", "elevenlabs"}:
            from .tts_providers import provider
            provider(self.cfg.tts_engine).segment(self.cfg, text, path)
        elif self.cfg.tts_engine == "kokoro":
            lang = "hi" if self.cfg.narration_language == "hi" else "en-us"
            if lang == "en-us" and re.search(r"[\u0900-\u097f]", text):
                raise ValueError("English voice selected, but this saved script contains Hindi. "
                                 "Create an English script or revise the saved narration before retrying")
            if lang == "hi" and not self.cfg.tts_voice.startswith("h"):
                raise ValueError("Hindi narration requires a Hindi Kokoro voice such as hm_omega")
            try:
                import onnxruntime as ort
                from kokoro_onnx import Kokoro

                if not self.cfg.kokoro_model.is_file() or not self.cfg.kokoro_voices.is_file():
                    raise RuntimeError("CONFIGURATION: run python scripts/setup_kokoro.py to download local voice files")
                if self.pipeline is None:
                    options = ort.SessionOptions()
                    options.intra_op_num_threads = 2
                    options.inter_op_num_threads = 1
                    session = ort.InferenceSession(str(self.cfg.kokoro_model), sess_options=options,
                                                   providers=["CPUExecutionProvider"])
                    self.pipeline = Kokoro.from_session(session, str(self.cfg.kokoro_voices))
                spoken = hindi_spoken_text(text) if lang == "hi" else text
                samples, rate = self.pipeline.create(spoken, voice=self.cfg.tts_voice,
                                                     speed=self.cfg.tts_speed, lang=lang)
                sf.write(path, samples, rate)
            except (ImportError, OSError) as error:
                raise RuntimeError("CONFIGURATION: Kokoro needs optional tts dependencies and local voice files. "
                                   "See SETUP.md. " + type(error).__name__) from None
        elif self.cfg.tts_engine == "piper":
            if not self.cfg.piper_model.is_file():
                raise RuntimeError("CONFIGURATION: Piper model and its .onnx.json must be installed")
            run(["piper", "--model", self.cfg.piper_model, "--output_file", path], input=text)
        else:
            if platform.system() != "Windows":
                raise RuntimeError("CONFIGURATION: SAPI is Windows-only; configure Kokoro or Piper")
            textfile = path.with_suffix(".txt")
            textfile.write_text(text, encoding="utf-8")
            # Read content as data; never interpolate narration into shell code.
            ps = Path(__file__).parent / "sapi.ps1"
            run(["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                 "-File", ps, "-TextFile", textfile.resolve(), "-OutputFile", path.resolve()])

    def synthesize(self, script, folder):
        segments, arrays, offset = [], [], 0.0
        if self.cfg.tts_engine == "indicf5":
            from .indicf5_speech import generate
            generate(self.cfg, [(scene.narration, folder / f"voice-{i:02d}.wav")
                                for i, scene in enumerate(script.scenes)])
        for index, scene in enumerate(script.scenes):
            path = folder / f"voice-{index:02d}.wav"
            if self.cfg.tts_engine != "indicf5":
                self.segment(scene.narration, path)
            check_audio(path)
            audio, rate = sf.read(path, dtype="float32")
            if audio.ndim > 1:
                audio = audio.mean(axis=1)
            if arrays and rate != arrays[0][1]:
                raise ValueError("TTS segment sample rates differ")
            if scene.target_seconds is not None:
                target = scene.target_seconds
                measured = len(audio) / rate
                speed = max(.5, measured / target)
                if speed > 1.5 or target - measured/speed > 1.5:
                    raise ValueError(f"Scene {index+1} measured {measured:.1f}s for a {target:g}s beat; "
                                     "revise its narration length to fit the Shorts template")
                fitted = folder / f"voice-{index:02d}-timed.wav"
                # Preserve pitch and leave a small safety margin before the next beat.
                run([self.cfg.ffmpeg, "-y", "-v", "error", "-i", path, "-af",
                     f"atempo={speed*1.01:.8f},apad,atrim=duration={target:.6f}", "-ar", str(rate), fitted])
                check_audio(fitted)
                audio, rate = sf.read(fitted, dtype="float32")
            duration = len(audio) / rate
            segments.append({"segment_id": index, "text": scene.narration,
                             "start": offset, "end": offset+duration})
            arrays.append((audio, rate))
            offset += duration
        sf.write(folder / "narration.wav", np.concatenate([a for a, _ in arrays]), arrays[0][1])
        check_audio(folder / "narration.wav")
        return segments
