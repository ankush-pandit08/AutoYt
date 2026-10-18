# Architecture

One Python process executes a linear, restartable job. A second process cannot acquire its OS-backed file lock. SQLite is the only persistent service. There is no application authentication, tenancy, backend HTTP API, Redis, Celery, Kubernetes, or cloud dependency.

## Modules

| Module | Responsibility |
| --- | --- |
| `config.py` | Typed `.env`, private/public configuration, three slots, local Ollama and 9:16 validation |
| `store.py` | Transactional SQLite/WAL, atomic JSON, reservations and upload progress |
| `discovery.py` | Real RSS/Atom parsing, freshness, niche scores, lexical duplicate detection |
| `net.py` | Public HTTPS retrieval, robots rules for articles, bounded downloads/retries |
| `research.py` | Text extraction, publication checks, evidence quotations, publisher independence |
| `ai.py` | Local Ollama JSON schema, bounded repair, grounding and editorial audits |
| `audio.py`, `sapi.ps1` | Offline Kokoro/Piper/SAPI, measured scene audio and clipping checks |
| `media.py` | Licensed local manifest, optional Pexels API, original graphics fallback |
| `video.py` | Procedural cards, FFmpeg scenes, animated ASS/SRT, covers and decode validation |
| `youtube.py` | Desktop OAuth, channel pin, configured visibility, resumable upload and reconciliation |
| `pipeline.py` | Checkpoints, recovery, output artifacts and failure handling |
| `scheduling.py`, `cli.py` | Three local slots, diagnostics, triggering, status and cleanup |

## Persistence and state

Schema version 2 migrates version 1 transactionally, preserving all earlier private upload records while allowing private/public visibility. `video_jobs` contains stage index, state, timestamps, retries, slot, dry-run flag, topic and unique video hash. `topics` reserves normalized fingerprints. `scheduled_runs` guarantees slot uniqueness. `upload_attempts` stores one upload lifecycle per job, and `youtube_uploads` has unique job/video IDs plus a SQL visibility constraint. `application_events` stores safe state transitions.

`artifacts` is the equivalent of separate source_documents/claims/scripts/media_assets tables for a single user: one typed JSON document per kind per job, storing all source evidence, script and licensed-asset provenance. It avoids a large relational model for data normally read together. Local JSON mirrors artifacts for human inspection. No tokens are written to events/logs; the upload session column must still be protected as a secret.

Stages: CREATED → DISCOVERING → RESEARCHING → SCRIPTING → SYNTHESIZING_AUDIO → ACQUIRING_MEDIA → RENDERING → VALIDATING → READY_TO_UPLOAD → UPLOADING → AWAITING_REVIEW (private) or PUBLISHED (public). FAILED and SKIPPED retain diagnostics and last checkpoint. Completion requires API confirmation of both the saved visibility and successful processing.

Each stage commits output before advancing its integer checkpoint. A process crash reruns the current incomplete stage, and does not repeat completed stages. Topic reservations are idempotent for the same job. A crash inside a stage can repeat work inside that stage (for example its audio segments); this is deliberate, keeping recovery simple. Upload recovery is stricter: a possibly issued insert is never blindly recreated.

The DB and mirrored JSON are not a cross-filesystem transaction. Atomic JSON replacement and DB checkpoint ordering make crashes recoverable, while a failed DB commit leaves the stage incomplete. Keep the database and job directory together when moving installations. Back up `.env`, `secrets/`, `data/database/`, and `output/jobs/` with private filesystem permissions while AutoYT is stopped.

## Scheduling and resources

APScheduler in-process cron triggers run three times daily in Asia/Kolkata. No scheduler job store is needed: triggers are fixed configuration, while handled slots persist in SQLite. Misfire grace is 55 seconds, and the callback independently rejects slots older than 60 seconds. There is no missed-slot backfill. A scheduler lock prevents multiple scheduler processes; a separate pipeline lock covers manual and scheduled work together. Daily limits count non-dry-run attempts including failed jobs, conservatively capped at three.

The machine must remain awake. Restart the scheduler through Task Scheduler/systemd if desired; use exactly one launcher. Failed jobs do not automatically spin forever; retries are bounded within providers and generation. Logs rotate. FFmpeg streams video, encodes one scene at a time with two threads, and does not load footage into Python memory. Downloads have byte limits; final output and cache cleanup remain conservative.

## Explicit tradeoffs

The evidence layer is stricter than many news bots, yet cannot prove truth. Source text is externally retrieved, exact quotations are checked, publishers are grouped conservatively and numbers must appear in each corroborating quote. Semantic entailment and editorial value still involve a model, so private human review remains essential. Near-duplicate detection is lexical; paraphrased stories may evade it. Generic stock matching uses search terms and manifest tags, not a vision model.

Caption timing is estimated within real measured scene audio, with no word forced alignment. The renderer supports crop-to-fill, gentle graphics zoom and short scene fades. It deliberately omits music mixing, blur-background layouts and complex transitions from this small version. This keeps the implementation maintainable; the code and documentation do not claim those optional effects were tested.
