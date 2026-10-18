# Running AutoYT on your own server

AutoYT is one personal Python scheduler plus a local Ollama runtime. It does not need this chat to remain open. Windows is the tested environment; Linux deployment has not yet been exercised. Fully unattended script writing remains unvalidated: moving the application to a server does not by itself resolve model writing failures.

Use a 64-bit machine with Python 3.11/3.12, FFmpeg including libass, internet access and sufficient disk space for models and videos. Based on memory pressure observed on the approximately 6 GiB development machine, 16 GiB RAM provides sensible headroom for the local 4B model, voice synthesis and editing. A GPU is optional.

1. Stop AutoYT on the old machine before transferring it. Copy source files, `.env`, `secrets/`, `data/database/`, required model/font files and `output/jobs/` privately. Preserve the database and upload records to avoid duplicates. Do not copy the Windows `.venv` to Linux; create a new environment there.
2. Install Python, FFmpeg and Ollama on the server. Keep Ollama on localhost and pull the model named in `.env`. Install AutoYT and its voice dependencies: `python -m pip install -e '.[tts]'`, then run `python scripts/setup_kokoro.py`. These commands use the server's virtual environment.
3. On Linux change Windows FFmpeg paths in `.env` to `FFMPEG=ffmpeg` and `FFPROBE=ffprobe`. Use paths relative to the project for models, voices, SQLite and outputs. Keep `NARRATION_LANGUAGE=hi`, `TTS_ENGINE=kokoro`, `TTS_VOICE=hm_omega`, and the saved Pixabay key. The copied Google token may refresh automatically; revoked/expired consent requires another authorization.
4. Run `python -m autoyt doctor --tts-test`, then a complete `python -m autoyt generate --dry-run`. Validate actual script, voice, footage and rendering on this server. A dry-run cannot upload. Fix any model writing failures before treating the installation as unattended.
5. Run exactly one `python -m autoyt scheduler` process from the project directory. Keep Ollama running too. Use Windows Task Scheduler or the systemd example in `SETUP.md` to start the scheduler after reboot and restart it after crashes. The machine must stay awake.

The schedule is 08:00, 14:00 and 20:00 Asia/Kolkata regardless of the server timezone. Three is the daily maximum attempt count, including failures; it does not promise three successful uploads. Old missed slots are skipped. No scheduler or server service is installed/running automatically in this checkout.

`ENABLE_UPLOAD=true` with `YOUTUBE_PRIVACY=public` selects public visibility for newly rendered jobs. Existing private jobs keep their saved visibility. Failed evidence, writing, audio or media checks stop that job before upload. Google credential expiry, platform restrictions and provider outages can still require attention. A real public Hindi upload has now passed on Windows; this does not validate unattended writing or Linux deployment.
