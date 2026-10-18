# AutoYT

Personal, local automation for **one** technology YouTube channel. Python, SQLite, optional Ollama or Gemini/Groq API, offline narration and FFmpeg. No account system, payments, queue service, or cloud hosting. An optional dashboard runs on localhost using Python's standard library.

Discovery → retrieved evidence → original script → voice → relevant licensed stock or original explanatory graphics → animated captions → cover → media validation → upload with the configured **private or public** visibility.

## Start on Windows

```powershell
powershell -ExecutionPolicy Bypass -File scripts/install.ps1
scripts/run.ps1 doctor
scripts/run.ps1 doctor --tts-test
scripts/run.ps1 generate --dry-run
scripts/run.ps1 jobs
```

Install FFmpeg (including FFprobe), Ollama and an offline English voice as described in [SETUP.md](SETUP.md). Python 3.11+ is required. CPU rendering uses two encoder threads and processes scenes sequentially. Plan on 8–16 GB RAM, several GB for models, and spare disk for intermediates. CPU inference/rendering can take minutes per Short; no dedicated GPU is required.

The checked-in `.env.example` uses Kokoro. The local `.env` may select the Windows SAPI fallback where Kokoro is unavailable. Voice quality varies; `doctor --tts-test` synthesizes a real short voice sample. Kokoro is loaded lazily. Piper accepts your separately installed binary and licensed ONNX voice model. In local-model mode, Ollama uses a configurable 4K context, keeps it warm between text calls, then explicitly unloads it before narration/rendering. In local-model mode, a small `all-minilm:22m` embedding model ranks source passages and unloads before the writer runs. API mode uses lexical passage ranking without loading Ollama; hosted providers receive public evidence and scripts. Provider quotas apply. Set `NARRATION_LANGUAGE=hi`, `SCRIPT_STYLE=hinglish` and `TTS_VOICE=hm_omega` for conversational Hinglish with the Hindi male voice. A tested Windows dependency snapshot is in `requirements-windows.lock.txt`; use `pyproject.toml` for Linux or a fresh installation.

## Local control room

Run `powershell -ExecutionPolicy Bypass -File scripts/start-autoyt.ps1` on Windows, or `python -m autoyt dashboard` on Linux. Open **http://127.0.0.1:8765**. Monitor daily results, live stages, failures, previews, sources and media credits; pause/resume the scheduler or retry a failed job. Pause state survives restart. Browser closure does not stop the process. See [DASHBOARD.md](DASHBOARD.md) and [MEDIA_LICENSES.md](MEDIA_LICENSES.md).

The default ten slots are **06:00, 07:40, 09:20, 11:00, 12:40, 14:20, 16:00, 17:40, 19:20 and 21:00 Asia/Kolkata**. Ten is an attempt limit, not a guarantee of ten uploads; failed quality checks never publish.

## Commands

| Command | Purpose |
| --- | --- |
| `python -m autoyt doctor [--tts-test]` | Diagnose binaries, model, directories, database and authorization prerequisites |
| `python -m autoyt trends` | Retrieve and rank real current RSS candidates |
| `python -m autoyt generate --dry-run` | Full local generation, never upload |
| `python -m autoyt generate --topic "Google processor"` | Filter discovered stories to a topic; never invent an unsupported supplied topic |
| `python -m autoyt run-once` | One pipeline, configured visibility when uploads are enabled |
| `python -m autoyt retry JOB_ID` | Resume from last completed checkpoint |
| `python -m autoyt jobs` | Show states, errors, topics and checkpoints (`list-jobs`/`status` aliases) |
| `python -m autoyt auth` | Initial Google consent, check and pin the single authorized channel |
| `python -m autoyt upload JOB_ID` | Upload a validated non-dry-run job with its saved visibility |
| `python -m autoyt dashboard` | Local monitoring UI and scheduler at http://127.0.0.1:8765 |
| `python -m autoyt scheduler` | Foreground scheduler: ten daily slots in Asia/Kolkata |
| `python -m autoyt cleanup` | Remove old reproducible intermediates; retain final outputs, evidence and recovery data |

Run from the project directory so `.env` and relative paths resolve correctly. `scripts/run.ps1` and `scripts/run.sh` set that directory. Nonzero CLI exit status indicates failure or a competing process.

## Operation

Configuration lives in `.env`, excluded from version control. `ENABLE_UPLOAD=false` and `YOUTUBE_PRIVACY=private` are the example defaults. After authentication and channel verification, enable uploads for normal runs. `YOUTUBE_PRIVACY=public` publishes future jobs once automated evidence and media checks pass. Visibility is saved in each job's metadata; changing the default never republishes older private jobs on retry. Dry-run jobs permanently prohibit uploading.

At most one pipeline can run across processes. Slot reservations and daily run counts are transactional. The default daily attempt limit is ten, including failures; quality takes priority. Missed old slots are skipped, with no backlog catch-up. Interrupted active work resumes before new work, subject to matching dry-run mode. Failed jobs remain available for explicit retry; new scheduled slots can try fresh topics. The scheduler must be running and the machine awake. The base installer does not register an OS task; `scripts/enable-autostart.ps1` separately enables current-user startup on Windows.

Each job saves `topic.json`, `research.json`, `script.json`, `timing.json`, `narration.wav`, `media_assets.json`, `subtitles.ass`, `subtitles.srt`, `cover.png`, `thumbnail.png`, `final.mp4`, `metadata.json` and `validation.json` under `output/jobs/JOB_ID`. JSON is written atomically; completed stages are checkpointed in SQLite. The review queue is `jobs` with state `AWAITING_REVIEW`.

## Quality, sources and originality

Google Trends India and technology RSS feeds supply dated candidates from the last 24 hours. Scores use available normalized metrics; missing search volume is excluded and weights renormalized. Title token overlap plus normalized text similarity provide lightweight near-duplicate detection over a 14-day window. This is conservative lexical matching, not a semantic embedding guarantee.

Research retrieves accessible related feed articles and follows cited official announcements. It requires at least two distinct publisher families including a primary source. Every claim needs exact quotations from an official primary document and independent corroboration. Exact quotation checks, lexical coverage, number checks, date checks, text deduplication and a separate model audit gate scripts before rendering. Cybersecurity prompts restrict output to defensive education. Unverifiable stories fail closed. This is an automated evidence filter, **not a guarantee of truth**; quotation similarity and a model can miss subtle contradictions or common-source reporting. Always review private uploads before publication.

On this approximately 6 GiB Windows machine, local Ollama writing repeatedly failed length or factual checks. A complete Gemini-driven Hinglish run has now passed research and script audits, generated a Hindi male Kokoro voiceover and licensed visuals, rendered a 58.1-second Short and completed a verified public upload. Its interrupted upload resumed from the saved session without a duplicate. This verifies one complete run; future stories and API quotas can still cause safe skips or failures. An optional `script_revision.json` in a failed job folder accepts `title`, `description`, `thumbnail_text` and six complete `narration` strings; it is an explicit editorial input, not evidence of successful unattended writing. Changes before audio generation pass through scripting checks again. An editorial narration outside the duration tolerance stops for revision rather than being silently replaced.

Source discovery is intentionally bounded to configured RSS and cited official documents, avoiding undocumented search scraping. Consequently some valid trends cannot find two suitable sources and are skipped or fail research. Expand permitted feeds/primary domains in configuration when appropriate; a source's official domain alone does not prove any specific claim.

Offline speech is generated per scene. Real WAV durations control scene cuts. Out-of-tolerance audio triggers one script revision, then fails. Captions use measured scene boundaries with phrase timing estimated by text length; they do **not** claim forced-aligned word timestamps. ASS adds high contrast outlines, keyword color and a brief scale/fade animation. Portrait H.264/AAC output is decoded and checked for dimensions, duration, frame rate, black frames, excessive silence, clipping, subtitles and content hash before upload.

The requested duration is a writing guide, not an exact video length. Natural narration up to 60 seconds is accepted; shorter narration is not padded to 45 seconds. Other quality gates still apply.

## Visuals and licensing

Default visuals are original procedural technology cards, circuit motifs and gentle camera motion. They explain script headlines and do not depict fake product demonstrations. Optional Pexels stock is generic illustrative B-roll; the description identifies it as illustrative. No copyrighted YouTube videos or popular music are downloaded. Background music is intentionally absent in this version.

Pexels requires your own valid API key; account/key availability is provider-controlled. Downloads are bounded and cached, and provenance/attribution is recorded. Review [Pexels API documentation](https://www.pexels.com/api/documentation/) and the [Pexels License](https://www.pexels.com/license/) before use. If unavailable, AutoYT tries Pixabay when configured, then original graphics. To configure local footage, copy `assets/footage/manifest.example.json` to `manifest.json` and record your actual permissions and matching tags. Local imports must declare CC0-1.0 or CC-BY-4.0 with the exact official license URL, creator attribution, `free_to_use=true`, and permitted use; an arbitrary "free" label is rejected. Example paths are documentation, not bundled media licenses.

Pixabay video and portrait-photo retrieval is implemented with `PIXABAY_API_KEY`. Searches are cached for 24 hours, query counts are bounded, and licensed media/credits are saved locally. Photo B-roll uses gentle pan/zoom. A bad key or provider failure falls back to original graphics without exposing the key in logs. Unsplash integration remains unimplemented. No stock keys are required for the original-graphics path.

Kokoro's model is Apache-2.0 per its [model card](https://huggingface.co/hexgrad/Kokoro-82M). Qwen's [Ollama model page](https://ollama.com/library/qwen3:4b-instruct) links its model/license information. Check any replacement voice/model and included system fonts under their own licenses. AutoYT does not redistribute font binaries or model weights.

## Upload safety and limitations

Google's official Python client handles [resumable uploads](https://developers.google.com/youtube/data/guides/using_resumable_upload_protocol). Session URI/progress and returned video IDs are persisted. Resume queries server progress first. Ambiguous initiation without a saved session reconciles the channel's latest 150 uploads using a unique job marker; if unresolved, it halts instead of issuing another insert. This may require manual review after a rare lost response; avoiding duplicate uploads takes priority. Do not remove job markers from descriptions while reconciliation is pending.

The API must confirm the saved visibility and successful processing before completion. Private uploads become `AWAITING_REVIEW`; public uploads become `PUBLISHED`. A visibility mismatch is a failure, with the returned ID preserved for reconciliation. Processing still underway can be confirmed by retrying the same job. The client uses an internal recovery flag covered by network-boundary tests; rerun tests when updating the Google client. Quota/auth/expired-session failures pause the job. Tokens and upload session URIs are credentials: keep the entire `data/` and `secrets/` directories private.

Both cover and thumbnail PNGs are generated locally. **AutoYT does not call `thumbnails.set` or claim a Shorts thumbnail was applied.** Review/select the available cover in YouTube's supported Studio/mobile workflow. [YouTube thumbnail guidance](https://support.google.com/youtube/answer/72431) distinguishes Shorts from ordinary video thumbnail uploads.

Synthetic disclosure is generated from the script's intended depiction and sent as `status.containsSyntheticMedia`. Ordinary AI narration and procedural explainers alone do not imply realistic fake events. Review the decision against [YouTube altered/synthetic content guidance](https://support.google.com/youtube/answer/14328491) before publishing. Set the made-for-kids configuration based on your actual audience, not the fact that the video is a Short.

## Test and troubleshoot

```powershell
.venv/Scripts/python -m pytest -q --basetemp=data/test-temp
.venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python scripts/smoke_render.py
```

Unit tests use deterministic data and replace external network boundaries. `scripts/smoke_render.py` actually synthesizes speech and renders/decodes a full-length local educational fixture, with no LLM, live research or upload. It adjusts the fixed test narration's timing to 45 seconds; production instead revises scripts. The result is explicitly labelled `local-smoke`. It is not proof of a successful real-news or YouTube integration run. See [VALIDATION.md](VALIDATION.md) for results from this machine.

Use `doctor` first. Missing models/binaries/consent need setup. Failed evidence means no upload, not permission to weaken checks. Failed media validation retains artifacts. `retry JOB_ID` resumes the saved stage. `cleanup` preserves outputs and essential recovery artifacts; cache and final-video retention are manual to avoid deleting private review material. Operating costs are local electricity, storage and bandwidth; no subscription/API payment is required by this application. Platform quotas and access restrictions still apply.

With `DAILY_SCRIPT_BATCH=true`, the morning plan discovers stories and prepares the day’s scripts once at `MORNING_PLAN_TIME` (05:00 recommended). Later video slots consume those saved scripts. The dashboard shows readiness and the scripts; insufficient verified material yields fewer videos. See [SETUP.md](SETUP.md#morning-only-script-preparation).
