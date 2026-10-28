import json
import importlib.util
import logging
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
        self.local_fallback = None
        self.usage = None

    def fallback_ready(self):
        return (self.cfg.tts_fallback_enabled and self.cfg.narration_language == "en"
                and self.cfg.kokoro_model.is_file() and self.cfg.kokoro_voices.is_file()
                and importlib.util.find_spec("kokoro_onnx") is not None
                and importlib.util.find_spec("onnxruntime") is not None)

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
        from .store import write_json
        usage_path = folder / "voice_provider_usage.json"
        if self.local_fallback is None and self.cfg.tts_engine in {"elevenlabs", "gemini"}:
            saved = json.loads(usage_path.read_text(encoding="utf-8")) if usage_path.is_file() else {}
            if (saved.get("requested_provider") == self.cfg.tts_engine
                    and saved.get("requested_voice") == self.cfg.tts_voice
                    and saved.get("active_provider") == "kokoro" and self.fallback_ready()):
                self.usage = saved
                self.local_fallback = Speech(self.cfg.model_copy(update={"tts_engine": "kokoro", "tts_voice": "am_michael"}))
        if self.local_fallback is not None:
            return self.local_fallback._synthesize(script, folder)
        try:
            return self._synthesize(script, folder)
        except RuntimeError as error:
            if (self.cfg.tts_engine not in {"elevenlabs", "gemini"} or not self.fallback_ready()
                    or str(error).split(":", 1)[0] not in {"TRANSIENT", "CONFIGURATION"}):
                raise
            self.usage = {"requested_provider": self.cfg.tts_engine, "requested_voice": self.cfg.tts_voice,
                          "active_provider": "kokoro", "active_voice": "am_michael", "language": "en",
                          "reason": str(error)[:300]}
            # Restart every segment with one voice; never mix hosted and local narration.
            write_json(usage_path, self.usage)
            logging.getLogger(__name__).warning("Hosted narration unavailable; using local English male voice for this job")
            self.local_fallback = Speech(self.cfg.model_copy(update={"tts_engine": "kokoro", "tts_voice": "am_michael"}))
            return self.local_fallback._synthesize(script, folder)

    def _synthesize(self, script, folder):
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
            duration = len(audio) / rate
            segments.append({"segment_id": index, "text": scene.narration,
                             "start": offset, "end": offset+duration})
            arrays.append((audio, rate))
            offset += duration
        sf.write(folder / "narration.wav", np.concatenate([a for a, _ in arrays]), arrays[0][1])
        check_audio(folder / "narration.wav")
        return segments
