# Voice generation settings

The dashboard Voice settings card selects Gemini TTS (default for new configurations), ElevenLabs Flash v2.5 or any existing local engine. Explicit existing environment selections remain in effect until saved preferences override them. Gemini speech generation code and retries are unchanged.

Set ELEVENLABS_API_KEY privately in the server environment or .env. ELEVENLABS_MODEL_ID is fixed to eleven_flash_v2_5. The key requires Voices: Read and Text to Speech permissions. No credentials are accepted or returned by the dashboard. Voices come from GET /v2/voices, with pagination and a five-minute catalogue cache. Labelled Indian male voices are ranked first; add a suitable licensed voice to your ElevenLabs library if none is available.

English maps to narration_language=en and standard script style. Hindi maps to hi and standard; Hinglish maps to hi and hinglish. New script generation, including morning preparation, loads saved preferences. Existing saved scripts are never translated. ElevenLabs infers Hindi/English from the actual transcript and uses multilingual inference for mixed Hinglish. Gemini uses its existing delivery prompt unchanged.

Preferences and per-provider voice choices are stored in data/database/voice-settings.json without credentials. Stale concurrent saves are rejected. Pipeline instances snapshot preferences so in-progress jobs retain their configuration. CLI and automated runs read saved selections.

Test Voice generates a short sample without saving preferences or starting a video/upload. It runs on the existing single worker, uses the pipeline lock, and permits one test per 30 seconds. Samples are private, expire after nine minutes, and are cleaned by the hosted retention sweep (or on subsequent standalone tests). Playback requires the same admin gateway access as the dashboard in OmniKit.

ElevenLabs uses official PCM 24 kHz output, converted to a validated PCM16 WAV for the existing scene timing, concatenation, FFmpeg and subtitle utilities. Completed segments are cached by provider/model/voice/language/text and credential fingerprint. There are no automatic retries of billable ElevenLabs synthesis requests and no automatic provider fallback. Missing keys, missing permissions, unavailable voices, quotas, rate limits, transport and validation failures stop clearly without echoing provider response bodies.

ElevenLabs free plan does not include commercial-use rights. Check your account license before publishing commercially: https://help.elevenlabs.io/hc/en-us/articles/13313564601361-Can-I-publish-the-content-I-generate-on-the-platform

Tests use isolated databases and uploads disabled. Never enable the original Windows scheduler alongside the hosted server scheduler.

## Verification (20 October 2026)

- Integrated Python suite: 269 passed, 2 optional media tests skipped; original AutoYT suite: 251 passed before final additions, with final voice/Gemini regressions passing separately.
- Real Gemini speech: valid non-silent audio; existing implementation unchanged.
- Real ElevenLabs Flash v2.5: English, Hindi and Hinglish PCM/WAV generation passed with the supplied account after Voices and Text to Speech permissions were enabled.
- Real native browser Test Voice playback passed for Gemini and ElevenLabs; settings reload and full process restart preserved provider, voice and Hinglish.
- Responsive UI checks passed at 390, 768 and 1366 px; no horizontal overflow.
- A six-scene render using real ElevenLabs audio passed FFmpeg codec, audio, duration, subtitles and synchronization quality checks. No YouTube test upload was made.
- Missing key, permissions, quota, rate-limit, unavailable voice and provider failure paths covered by unit tests; credits were not deliberately exhausted.
- OmniKit TypeScript checks, changed API ESLint, Python undefined-name lint, new-module Ruff lint, seven admin-gateway tests, API production build, web production build and AutoYT wheel build passed. Web build used one worker to fit local memory.
- No runtime dependency added; no production deployment or scheduler ownership change made.
