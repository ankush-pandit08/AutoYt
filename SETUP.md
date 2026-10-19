# Setup

## Windows prerequisites

1. Install Python 3.11 or 3.12 and select **Add Python to PATH**. Use 64-bit Python.
2. Install a trusted FFmpeg build with `ffmpeg.exe` and `ffprobe.exe`. Add its `bin` directory to PATH or set absolute `FFMPEG` and `FFPROBE` paths in `.env`. A downloaded build was placed in `data/tools` on the development machine; this ignored directory is not part of source distribution. Review the build's license if redistributing binaries.
3. Install [Ollama for Windows](https://ollama.com/download/windows), start it, then run `ollama pull qwen3:4b-instruct` and `ollama pull all-minilm:22m`. The small embedding model ranks evidence passages locally; it does not decide factual truth. The [4B instruct model](https://ollama.com/library/qwen3:4b-instruct) is configurable. Keep the server bound to localhost. Model weights require a one-time download; no paid inference service is used.
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

Pixabay is optional. AutoYT tries local licensed assets and configured Pexels videos, then Pixabay video/photo search, then original graphics. Each run uses at most two different Pixabay queries and downloads at most one asset per scene. Search responses are cached for 24 hours as required by the provider; already-downloaded media is reused locally. Portrait photos can use pan/zoom; lower-resolution footage is skipped. Source URLs, license references and creator credits are recorded locally in each job; YouTube descriptions are left empty. Generic stock is labelled illustrative on screen. API failures fall back to original graphics without printing credentials.

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

The default schedule has ten slots in Asia/Kolkata: **06:00, 07:40, 09:20, 11:00, 12:40, 14:20, 16:00, 17:40, 19:20, 21:00**. `MAX_VIDEOS_PER_DAY=10` caps live attempts, including failed/skipped runs. Ten successful uploads are not guaranteed.

Start `powershell -ExecutionPolicy Bypass -File scripts/start-autoyt.ps1` on Windows. It starts Ollama when available and the localhost dashboard with its scheduler. The browser opens http://127.0.0.1:8765. On Linux run `python -m autoyt dashboard`. Keep the machine awake. The UI provides persistent pause/resume, progress, previews, source/license records and retry controls. Do not also start the separate `scheduler` command.

To enable Windows sign-in startup explicitly, run `powershell -ExecutionPolicy Bypass -File scripts/enable-autostart.ps1`. It creates a limited-privilege current-user task named `AutoYT Personal Dashboard`, with three restart attempts after failure. It starts after sign-in, not before sign-in. Re-run it if you move the checkout. To remove it, use `Unregister-ScheduledTask -TaskName 'AutoYT Personal Dashboard' -Confirm:$false`. Removing the task does not stop an already running process; pause from the dashboard first.

Linux systemd example (replace paths/user):

```ini
[Unit]
Description=AutoYT personal scheduler
After=network-online.target

[Service]
Type=simple
User=YOUR_USER
WorkingDirectory=/home/YOUR_USER/AutoYT
ExecStart=/home/YOUR_USER/AutoYT/.venv/bin/python -m autoyt dashboard
Restart=on-failure
RestartSec=30

[Install]
WantedBy=multi-user.target
```

The base installer does not register a service/task. The optional startup script does so explicitly. Run exactly one dashboard or standalone scheduler after validating your configuration. For Linux remote access keep the loopback binding and use an SSH tunnel as described in SERVER.md. `ENABLE_UPLOAD=false` still allows scheduled local generation, while keeping all YouTube upload endpoints disabled.

## Storage and maintenance

Run `cleanup` periodically to remove old scene MP4/PNG and per-scene voice intermediates. It preserves narration, final videos, covers, research, subtitle files, metadata, SQLite and upload sessions for review and recovery. Cached stock and final-video retention is manual. Stop AutoYT before backing up/moving the database, including its WAL companions, or use SQLite's backup tooling. A malformed `.env` fails startup. Run commands from the project root, and do not share your `.env`, token files or upload-session database.


### Optional Groq API and Hinglish scripts

Create an API key at https://console.groq.com/keys using your own account. Stay on the free plan if you want no API charges; quotas and model access depend on your account. Save these settings in the local `.env` file (never commit the real key):

```dotenv
LLM_PROVIDER=groq
GROQ_API_KEY=your_key_here
GROQ_MODEL=qwen/qwen3.8-27b
NARRATION_LANGUAGE=hi
SCRIPT_STYLE=hinglish
TTS_VOICE=hm_omega
```

The configured Qwen model is currently a preview model; replace `GROQ_MODEL` if the provider retires it. Groq receives public news evidence and generated scripts for generation and audits. Local OAuth credentials and media API keys are not included. API mode uses lightweight lexical passage ranking instead of local embeddings, retains exact quotation and factual checks, and does not load Ollama. Kokoro and FFmpeg still run locally. Quota errors stop the run safely with bounded retries; there is no automatic paid-provider fallback. Free quotas may not cover ten videos every day.

Hinglish uses Hindi sentence structure with common English tech words. Hindi is written in Devanagari for the Hindi Kokoro voice. Restart the dashboard after `.env` changes. While initially connecting the API, keep auto mode paused; run a complete test before resuming it.


### Gemini API alternative

Save `LLM_PROVIDER=gemini`, `GEMINI_API_KEY=your_key`, and `GEMINI_MODEL=gemini-3.8-flash` in `.env`. Gemini uses Google's structured JSON endpoint with local schema validation and the same factual/publication checks. Hosted mode does not load Ollama. Google lists a free tier for this model; account quotas still apply, and an account with paid billing can incur charges. Keep your project on the free tier for free-only operation. Public news evidence and scripts are sent to Google, whose free-tier terms allow use of content to improve products. OAuth and media keys are not sent. API keys stay in `.env`, excluded from Git. After changing settings, restart the dashboard. Keep automatic uploads paused until the connection and full video test succeed.


### Morning-only script preparation

Set `DAILY_SCRIPT_BATCH=true` and `MORNING_PLAN_TIME=05:00` to discover ranked technology stories and prepare up to ten distinct, verified Hinglish scripts once each morning. Preparation must start before the earliest video slot. At each `SCHEDULE_TIMES` slot, AutoYT uses its assigned saved script and research, then generates narration, sources licensed media, renders, validates and uploads. It does not rediscover topics or write replacement scripts at video time. Narration that exceeds the duration bounds stops safely rather than triggering new writing. Manual Run now and preview commands remain explicit on-demand workflows.

The dashboard shows the morning plan and saved scripts. Plans are dated in the configured timezone and saved under `data/database/daily-plans`. Discovery snapshots and completed entries survive interruptions. If insufficient stories pass verification or API quotas are exhausted, the plan may contain fewer than ten scripts; missing slots are skipped. API failures do not trigger repeated daily discovery. An advanced manual `autoyt plan-day` command can initialize today's plan outside the normal schedule. A completed/failed daily plan is not regenerated automatically.

Keep the machine awake with AutoYT running at 05:00. If morning preparation is missed, later video slots do not discover replacements. Old plans are never reused for a new day. Preparation and video generation share the same process lock; a still-busy preparation can cause an overlapping slot to be skipped. The morning list reflects the sources available then, rather than live trends throughout the day. The engaging script prompt uses a factual curiosity hook, immediate payoff, practical takeaway and a relevant comment question. It does not promise viral results.

`scripts/preview_gemini_voice.py` generates an isolated Gemini TTS sample using the existing key without changing the scheduled voice. Use `--text-file` and `--output` for a script preview. Provider free-tier quotas apply.


### Default Gemini narration

For Gemini 2.5 Flash speech, set `GEMINI_TTS_MODEL=gemini-2.5-flash-preview-tts`, `TTS_ENGINE=gemini` and `TTS_VOICE=Puck`. The app uses Google's Generate Content endpoint and converts the returned raw PCM audio into WAV for validation and editing. This is a speech model; `gemini-2.5-flash` is the text model. The key remains only in the local `.env` file. This integration also retains support for the newer speech endpoint described below.

Set `TTS_ENGINE=gemini`, `TTS_VOICE=Puck` and `GEMINI_TTS_MODEL=gemini-3.8-flash-tts` to use the approved Indian male Hinglish delivery for new videos. The existing `GEMINI_API_KEY` is reused. English tech names and Hindi text are sent verbatim to Gemini; voice style is separate metadata, so delivery instructions are not spoken. Each scene is measured separately for subtitle timing. The selected model controls pacing; the local `TTS_SPEED` setting applies to local engines.

Validated scene audio is cached by exact text, voice, model and delivery style. Interrupted jobs can reuse completed scenes without repeating those API requests. Quota or access failures stop safely and retain the checkpoint. There is no silent change to another voice or paid model. Speech has its own provider quotas; ten video slots do not guarantee enough free speech requests. `doctor --tts-test` makes a small real speech request and checks its audio. Restart the dashboard after changing the engine or voice.

The live configuration uses `GEMINI_MODEL=gemini-3.6-flash` for writing and audits after the newer Flash text model reached its daily free quota and Flash-Lite drafts failed content checks. The separate speech model remains `gemini-3.8-flash-tts`. Model quotas differ; either may stop a run safely when exhausted.

Concise scripts may contain 60–115 words; the prompt targets 85–100 words. The final measured audio and video must still fit the configured maximum of 60 seconds. A shorter script does not need extra filler.


Stock selection checks meaningful provider tags and omits generic search fillers such as “close up.” Unrelated matches fall back to original explanatory graphics. Licensing and visual relevance are separate checks; provider metadata alone is not a guarantee of accurate imagery.

The YouTube transport preserves HTTP 308 as resumable progress while setting a bounded timeout. If a rejected render already has a pending upload, an explicit upload block preserves its recovery record until cancellation/deletion has been reconciled. A corrected render must not be resumed into the prior upload session. Cancelled video IDs can be excluded from reconciliation to avoid selecting stale playlist entries.


### Local IndicF5 Hinglish narration

IndicF5 replaces only speech generation; Gemini script writing and fact audits still use the configured API and its quotas. This integration uses the public Hindi-English code-switch model at a pinned revision, with no hosted voice API calls. Hindi words should remain in Devanagari and English tech terms in Latin letters.

On Windows, from the project folder:

```powershell
python -m venv .venv-indicf5
.venv-indicf5/Scripts/python.exe -m pip install torch==2.6.0 torchaudio==2.6.0 --index-url https://download.pytorch.org/whl/cpu
.venv-indicf5/Scripts/python.exe -m pip install -r requirements-indicf5.txt
.venv/Scripts/python.exe scripts/setup_indicf5.py
```

On Linux, use `.venv-indicf5/bin/python` for the isolated environment and `.venv/bin/python` for AutoYT. CPU inference works without NVIDIA hardware. The model is approximately 1.35 GB; runtime packages use additional disk and RAM. Generation can take several minutes on a low-memory CPU machine. One isolated worker loads the model for all scenes and exits before video rendering. No Torch dependencies are added to AutoYT's main environment.

Place an authorized clean 1–10 second male reference WAV at `data/voices/indicf5-reference.wav`, and its exact spoken transcript at `data/voices/indicf5-reference.txt`. A saved, approved synthetic narration sample may be reused; retain its provenance locally. The model's bundled reference voices are not used automatically.

Set in `.env` after the runtime and model are ready:

```dotenv
TTS_ENGINE=indicf5
TTS_VOICE=male_reference
INDICF5_STEPS=16
INDICF5_TIMEOUT_SECONDS=3600
```

Optional overrides: `INDICF5_PYTHON`, `INDICF5_MODEL_DIR`, `INDICF5_REFERENCE_AUDIO`, `INDICF5_REFERENCE_TEXT_FILE`. Run `python -m autoyt doctor --tts-test` to generate and validate a voice sample. The engine caches audio by exact text, reference audio/transcript, model manifest, steps and speed. It runs with Hugging Face offline mode and loads reviewed local code and model files. It never falls back to Gemini narration. Failed inference reports a configuration error; timeout preserves completed audio for retry. Inference diagnostics are saved locally in `data/cache/indicf5-worker-error.log`.

Model: https://huggingface.co/Tharshan/indicf5_hindi-english_code_switch (Apache-2.0); vocoder: https://huggingface.co/charactr/vocos-mel-24khz. Weight and provenance files stay out of GitHub. Higher step counts can improve quality at the cost of CPU time. Final videos still need to pass the existing audio and duration checks.


IndicF5 CPU tuning preserves the model weights, FP32 precision, reference voice, step count and classifier-free guidance. `INDICF5_THREADS` controls CPU parallelism (default 2); benchmark the machine before raising it. The worker reuses deterministic text and rotary conditioning only within a single utterance, and clears these caches between scenes. Existing WAV caches are preserved when changing threads. Timing diagnostics for newly generated audio are written to `data/cache/indicf5-last-run.json`, separating model loading from each segment's inference time. Thread tuning and conditioning reuse do not enable quantization, lower precision or fewer generation steps.


On the tested Windows PC (Ryzen 5 5500U, about 6 GB RAM), six threads were the fastest of 2/4/6 tested: 3.56 seconds per model pass versus 5.21 at two threads. A fresh-process test of the same 5.1947-second Hinglish sample fell from the earlier recorded 397.58 seconds to 145.703 seconds including process startup/loading; inference itself took 120.063 seconds. Both used the same reference, FP32, 16 steps, guidance 2 and speech speed 1.05. Exact conditioning reuse was bitwise equal; cross-thread floating-point outputs passed close numerical comparison. Timing depends on machine load and memory pressure, and CPU synthesis remains slower than real time. The local `.env` selects `INDICF5_THREADS=6`; the portable example stays at two threads until measured on the target machine. `scripts/benchmark_indicf5.py` runs in the isolated runtime and compares threads without creating video jobs; run it only while the application is idle. Existing cached voice segments are reused on retry, even after thread tuning.

YouTube access-token expiry is handled by automatic refresh. Temporary refresh transport/service failures receive three bounded attempts and a TRANSIENT error; they do not imply revoked consent. An actual invalid_grant requires `python -m autoyt auth`, which can reopen consent even when the old token is rejected. Other credential/client errors request an OAuth setup check. A stage-7 job can be retried from its saved upload checkpoint without regenerating its video.
