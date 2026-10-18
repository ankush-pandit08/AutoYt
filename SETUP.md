# Setup

## Windows prerequisites

1. Install Python 3.11 or 3.12 and select **Add Python to PATH**. Use 64-bit Python.
2. Install a trusted FFmpeg build with `ffmpeg.exe` and `ffprobe.exe`. Add its `bin` directory to PATH or set absolute `FFMPEG` and `FFPROBE` paths in `.env`. A downloaded build was placed in `data/tools` on the development machine; this ignored directory is not part of source distribution. Review the build's license if redistributing binaries.
3. Install [Ollama for Windows](https://ollama.com/download/windows), start it, then run `ollama pull qwen3:4b-instruct`. The [4B instruct model](https://ollama.com/library/qwen3:4b-instruct) is configurable. Keep the server bound to localhost. Model weights require a one-time download; no paid inference service is used.
4. Run `powershell -ExecutionPolicy Bypass -File scripts/install.ps1` in the project directory. It creates `.venv`, installs the package, copies `.env.example` if needed and runs diagnostics. It does not install system packages or register an OS scheduler.
5. Set up an offline voice as below. Run `scripts/run.ps1 doctor --tts-test` and then `scripts/run.ps1 generate --dry-run`.

On this development checkout, a portable CPU Ollama runtime and model were placed under `data/`. After reboot, run `powershell -File scripts/start-ollama.ps1` in a separate terminal before starting AutoYT. It binds to localhost, disables cloud features, limits loaded models/concurrency, and keeps model data in the workspace. A normal Ollama desktop installation is an alternative; do not start both on the same port.

### Kokoro (preferred)

```powershell
.venv/Scripts/python -m pip install -e '.[tts]'
.venv/Scripts/python scripts/setup_kokoro.py
```

AutoYT uses the lightweight [Kokoro ONNX runtime](https://github.com/thewh1teagle/kokoro-onnx) with CPU execution limited to two threads. Setup downloads the INT8 model and voice pack to `data/models/kokoro`, verifies their pinned SHA-256 checksums, and adds an OFL-licensed Hindi font under `data/fonts`. The runtime includes an espeak-ng loader; no Torch installation or voice API key is required. Once downloaded, synthesis uses local files.

For the requested Hindi male voice, set these local `.env` values:

```dotenv
TTS_ENGINE=kokoro
NARRATION_LANGUAGE=hi
TTS_VOICE=hm_omega
TTS_SPEED=1.05
```

`hm_psi` is the other Hindi male voice. Run `.venv/Scripts/python scripts/preview_hindi_voice.py` to generate both local samples. `doctor --tts-test` also synthesizes a real sample in the selected language. English uses `NARRATION_LANGUAGE=en` with an English voice such as `af_heart`. Hindi writing prompts, video metadata and Devanagari subtitle/card rendering follow the selected language; a successful voice test does not validate automatic news writing.

### Pixabay B-roll

Log in to [Pixabay's API documentation](https://pixabay.com/api/docs/) to see your API key. Save it only in the local `.env` file:

```dotenv
PIXABAY_API_KEY=your_actual_key
```

Pixabay is optional. AutoYT tries local licensed assets and configured Pexels videos, then Pixabay video/photo search, then original graphics. Each run uses at most two different Pixabay queries and downloads at most one asset per scene. Search responses are cached for 24 hours as required by the provider; already-downloaded media is reused locally. Portrait photos can use pan/zoom; lower-resolution footage is skipped. Source URLs, license references and creator credits are recorded in each job and its YouTube description. Generic stock is labelled illustrative on screen. API failures fall back to original graphics without printing credentials.

### Other offline voices

Windows: set `TTS_ENGINE=sapi`. It uses an installed English US system voice through COM and needs no model download. It is a lower-quality fallback; the selected voice is the first matching English US voice. Windows Settings → Time & language → Speech can install an English voice if absent. SAPI is not available on Linux.

Piper: install a compatible local `piper` executable and a voice's `.onnx` plus matching `.onnx.json`. Check that voice's model-card license. Set `TTS_ENGINE=piper` and `PIPER_MODEL=assets/voices/en_US.onnx`. The default filename is a configuration location, not a bundled model. AutoYT passes narration to stdin and receives a local WAV. No network TTS fallback exists.

## Linux

Install Python 3.11+, venv support, FFmpeg/FFprobe and espeak-ng through your distribution's package manager. Install Ollama using its official instructions and pull the configured model. Run `bash scripts/install.sh`, optionally install `.[tts]`, then use `bash scripts/run.sh doctor --tts-test`. Linux execution is supported by code paths and configuration but has not been exercised on this Windows development host.

## Google authorization (required for uploads)

Follow Google's [installed application OAuth guide](https://developers.google.com/identity/protocols/oauth2/native-app) and [YouTube authentication guidance](https://developers.google.com/youtube/v3/guides/authentication). Console labels can change; the required resource is an OAuth **Desktop app** client, not an API key or service account.

1. Open [Google Cloud Console](https://console.cloud.google.com/) and create/select your own project.
2. In **APIs & Services → Library**, enable **YouTube Data API v3**.
3. Configure the project's **Google Auth Platform** branding, support/contact email and audience. For a personal Google account use External, and add your own Google account as a test user while testing.
4. In Data Access/scopes, configure `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube.readonly`. The second scope is needed to check channel identity, privacy/processing, and reconcile uncertain uploads.
5. Create an OAuth client with application type **Desktop app**. Download its JSON to `secrets/client_secret.json`; never commit it or paste it into a chat.
6. Run `scripts/run.ps1 auth`. This opens Google's consent flow with a loopback redirect. Choose the account/channel you actually own. The command verifies exactly one channel, stores a local refresh token, and pins its channel ID into `.env` if not already set. A different already-pinned channel is rejected.
7. Confirm `YOUTUBE_CHANNEL_ID` and the actual audience setting `YOUTUBE_MADE_FOR_KIDS`. AutoYT defaults the latter to false for general technology explainers; change it if your real content is child-directed.
8. Keep upload disabled while reviewing a local dry run. Once satisfied, **explicitly set `ENABLE_UPLOAD=true` in `.env`** and run a new `run-once` job for your authenticated private upload test. Dry-run job IDs cannot be uploaded.
9. Inspect YouTube Studio and the saved upload record. Keep `YOUTUBE_PRIVACY=private` for manual review, or set `YOUTUBE_PRIVACY=public` to publish future jobs after automated validation. The selected visibility is frozen in each job's metadata and verified through the API. Changing this default does not republish earlier private videos.

Google's [testing audience documentation](https://support.google.com/cloud/answer/15549945) explains limited authorization lifetime in testing (including the seven-day refresh-token limitation for applicable scopes). Reauthorization may therefore be required. App verification, quota and account restrictions are controlled by Google. Do not assume unlimited access or enable billing automatically. AutoYT sends an explicit visibility setting and confirms the actual result using the [YouTube API](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert).

Only `auth` launches interactive consent. Normal scheduled runs stop on missing/revoked credentials. On Linux, token files are created with mode 0600. On Windows, protect `secrets/` and `data/database/` with your user account's NTFS permissions; Python's chmod is not a complete Windows ACL policy. Session URIs stored in SQLite are also sensitive. Use encrypted disk/private backups as appropriate.

### Interrupted uploads

Run `retry JOB_ID`, preserving the original database, video and marker. The saved session resumes through Google's client. If a video ID was returned, retry checks that ID instead of re-uploading. If initiation may have happened without a session URI, the app searches the latest 150 channel uploads for its marker. If no result is found, it blocks that job. Check Studio before deciding how to resolve the upload record; do not delete records and generate a duplicate automatically.

## Schedule

The configured slots are **08:00, 14:00 and 20:00 Asia/Kolkata**. Start `scripts/run.ps1 scheduler` (or `bash scripts/run.sh scheduler`). Leave the process running and disable sleep during generation. Old missed slots are skipped. `MAX_VIDEOS_PER_DAY=3` is a maximum, not a promise that unreliable stories will be uploaded.

Windows Task Scheduler can launch this one foreground scheduler at logon: Program `D:\LoopYt\.venv\Scripts\python.exe`, Arguments `-m autoyt scheduler`, Start in `D:\LoopYt`. Select **Do not start a new instance**, and optionally restart on failure. Prefer running as your own logged-in account when using SAPI. Set paths for your checkout. Do not also create three independent cron/time triggers for the same installation.

Linux systemd example (replace paths/user):

```ini
[Unit]
Description=AutoYT personal scheduler
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/AutoYT
ExecStart=/home/YOUR_USER/AutoYT/.venv/bin/python -m autoyt scheduler
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

No service/task is installed or started automatically. Start only one scheduler after validating your configuration. `ENABLE_UPLOAD=false` still allows scheduled local generation, while keeping all YouTube upload endpoints disabled.

## Storage and maintenance

Run `cleanup` periodically to remove old scene MP4/PNG and per-scene voice intermediates. It preserves narration, final videos, covers, research, subtitle files, metadata, SQLite and upload sessions for review and recovery. Cached stock and final-video retention is manual. Stop AutoYT before backing up/moving the database, including its WAL companions, or use SQLite's backup tooling. A malformed `.env` fails startup. Run commands from the project root, and do not share your `.env`, token files or upload-session database.
