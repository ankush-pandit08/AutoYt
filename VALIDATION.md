# Validation on this Windows machine

This report distinguishes executed local tests from untested live services. Updated 18 October 2026, Asia/Kolkata.

## Passed

- Python 3.12 virtual environment installed in `.venv`; `pip check` reported no broken requirements.
- `ruff check src tests scripts` passed; Python source compilation passed.
- **103 pytest tests passed**, including real FFmpeg rendering/decoding at a small test resolution with both generated graphics and animated JPEG photo B-roll. Provider boundary tests are distinguished from the live checks below. Final lint passed. Checks also cover public visibility, private history preservation, transactional database migration, compact Hindi model requests and pronunciation aliases that preserve exact on-screen package identifiers.
- Tests cover RSS parsing/relevance/ranking, duplicates, configuration, exact evidence/primary-source/independence/number checks, extraction/date conflict, script schema and bounded malformed-output retries, silent/clipped audio, missing voice model, graphical fallback, caption boundaries, process locks, SQLite checkpoint recovery, daily/slot limits, IST/missed-slot behavior, and dry-run orchestration.
- Upload tests cover private metadata, duplicate prevention, 429/5xx backoff, authorization/quota failures, interrupted sessions, ambiguous initiation/reconciliation, privacy and processing gates, disabled uploads, dry-run blocking, revoked refresh credentials and changed files.
- Live HTTP 200 responses were received from Google Trends India, Google Blog RSS, Microsoft Blog RSS and The Verge Atom feed. Live parsing accepted two fresh technology candidates at test time; only The Verge had qualifying stories in that 24-hour window.
- Real Windows English SAPI speech synthesis passed. SAPI COM access requires execution outside this task's Windows sandbox; this is an agent environment restriction, not a placeholder TTS implementation.
- FFmpeg and FFprobe were downloaded from Gyan's release essentials build into `data/tools` and executed successfully.
- **`output/jobs/local-smoke/final.mp4`** was rendered with real synthesized narration, six original graphic scenes, motion, animated ASS captions, cover and thumbnail. FFprobe confirmed **45.0 seconds, 1080×1920, 30 FPS, H.264/yuv420p, AAC audio**. Full decode/black/silence/peak/subtitle checks passed. A rendered frame was visually inspected.
- This sample uses fixed educational text, 102 words, and is explicitly marked as a local renderer fixture. Its research file says live news was not verified. Its voice is Windows SAPI, not Kokoro. No upload endpoint was used.
- A real live `generate --dry-run` execution discovered a technology story and correctly **refused to continue** at research because three independent sources including an official primary source were unavailable. Job `171cc0fe876a4f81` retained stage 1 and its source requirement error. No fabricated corroboration or video upload followed.
- Official portable Ollama v0.40.6 was installed under `data/tools/ollama`, with CPU libraries only, and the `qwen3:4b-instruct` model was downloaded to `data/models`. Ollama is bound to localhost with cloud features disabled. `scripts/start-ollama.ps1` reproduces that local runtime configuration after restart.
- **Real local Ollama structured inference passed** with the configured 4K context: HTTP 200 and schema-compliant `{"ready":true}`. The probe unloaded the model afterward. This verifies the runtime/JSON integration, not a successful evidence-backed news script.
- Google desktop OAuth consent completed. The owner API confirmed the single pinned channel. Example configuration defaults to disabled uploads and private visibility; credentials and channel configuration are excluded from the repository.
- Real-news research passed exact quotations and independent corroboration using ConnectWise, BleepingComputer and The Hacker News. An explicit **Codex source-reviewed editorial revision** passed the local `ScriptAudit` with no unsupported sentences, `misleading=false` and `defensive_only=true`. This was necessary after automatic local writing repeatedly failed quality checks.
- A corrected private upload test reused the audited script, verified sources and measured SAPI narration, with an explicit parent-revision record. The corrected renderer uses the verified final title on screen. Its real **48.533-second, 1080×1920, 30 FPS H.264/AAC** output passed all decode, audio, black-frame and subtitle checks. A final frame was visually inspected. The duration policy now accepts natural narration up to 60 seconds without padding to the nominal target.
- A separate owner API check confirmed both private test videos belong to the pinned channel and report **privacy=private, uploadStatus=processed, processingStatus=succeeded**. Local confirmation and private video identifiers are excluded from this repository. No thumbnail API call occurred.
- A later source-reviewed Hindi Short uploaded publicly. A separate owner API query confirmed channel ownership, **privacy=public, uploadStatus=processed, processingStatus=succeeded**, and YouTube duration `PT50S`. The local file is **49.9 seconds**, with Kokoro male Hindi narration, four licensed Pixabay assets, animated Hindi captions and explanatory graphics. All media checks passed and a six-frame contact sheet was inspected. The script was audited against SafeDep/The Hacker News evidence and supplemented by the CSA research note. This remains an editorially reviewed run, not proof of unattended writing. Local job and API artifacts are excluded from this repository.

## Limits and remaining checks

- **Fully unattended local script generation is not validated on this hardware.** The 1.7B and 4B local writing attempts failed length, completeness or grounding checks. The completed private test used the explicitly recorded editorial revision, not a claim of successful unattended Ollama writing. Do not treat this result as production readiness for unattended scheduled publishing.
- **Kokoro Hindi integration passed on Windows:** pinned/checksummed INT8 model plus voice pack, `kokoro-onnx 0.6.1`, CPU only, two inference threads. `hm_omega` and `hm_psi` generated real Hindi WAV previews at speed 1.05, with durations 15.807s and 16.168s; neither was silent or clipped. Local configuration now selects Hindi male `hm_omega`. Piper remains untested live.
- **Pixabay live integration passed:** the locally saved key returned a suitable technology video, downloaded and probed successfully. Its public source is `https://pixabay.com/videos/id-14900/`; the key is excluded from saved artifacts and logs. A local 15.833-second 1080×1920 H.264/AAC preview combines this illustrative footage, the Hindi Kokoro narration and shaped animated Hindi captions. All media checks passed. This is a local voice/footage preview, not a new YouTube upload or a claim of successful automatic Hindi news writing.
- Pexels live API access was not tested because no API key was supplied. Original graphics work without one; local assets require your licensed manifest.
- Linux execution was not tested on this Windows host.
- Ten IST slots and the daily attempt cap are configured and tested. The dashboard and scheduler run on localhost. A limited-privilege current-user Windows sign-in task was registered. Linux service deployment remains untested. Missed past slots are not backfilled.
- Caption phrases are estimated within measured scene boundaries, not word forced alignment. Editorial/source checks remain imperfect automated filters. Optional music, blur layouts and advanced transitions are omitted.
- Startup logs reported approximately 5.9 GiB total RAM. The initial 16K context probe exhausted memory; the application was changed to a configurable 4K context, three bounded source documents, two–three claims, compact writing evidence and immediate model unloading. The real 4K probe passed (`data/ollama-smoke-4k.json`). The earlier failed probe is retained separately in `data/ollama-smoke.json`. Memory pressure from other applications can still interrupt CPU inference.

## Reproduce

```powershell
.venv/Scripts/python -m pytest -q --basetemp=data/test-temp
.venv/Scripts/python -m ruff check src tests scripts
.venv/Scripts/python -m autoyt doctor --tts-test
.venv/Scripts/python scripts/smoke_render.py
.venv/Scripts/python -m autoyt generate --dry-run
```

The live discovery result is time-dependent and may safely skip or fail research. Pytest temporary files use the workspace because Windows sandbox temporary-folder rename restrictions otherwise interfere with atomic artifact tests.

## Dashboard and free-media policy

- Full suite: 103 tests passed with local socket access; lint passed. HTTP tests verify loopback binding, hostile Host/Origin rejection, anti-CSRF checks, persistent pause state, complete daily counters and video byte ranges.
- Existing media sets for three completed live jobs passed the recorded approved-free-license policy. This checks provenance records, not independent copyright ownership or model/property releases.
- The live dashboard displayed actual SQLite jobs, configured Hindi voice, public upload mode, ten slots and local source/license credits. Browser pause/resume controls were exercised, and a real 49-second Short opened in the video player.
- Windows sign-in startup was registered and its task configuration checked. A reboot/sign-in test has not been performed.
