# Motion graphics implementation plan

## Goal and boundaries

Offer Normal, Motion Graphics and Hybrid for new Shorts. Run Remotion locally, reuse existing research and narration, preserve evidence/licensing gates, and keep all work local until the owner explicitly requests a GitHub push. No generated JavaScript, cloud render service, new AI provider or paid template is involved.

## Delivery steps

1. **Selection and persistence.** Add a dashboard rendering selector, optional Normal fallback, and a versioned saved default. Freeze each new job's rendering selection so changing defaults cannot alter retries. Legacy jobs retain Normal rendering. Scheduled runs read the current saved default.
2. **Deterministic scene planning.** Use existing scene headlines, narration and measured scene times. Full Motion Graphics animates every scene. Hybrid animates the hook, takeaway and scenes without footage, retaining licensed footage for the others. Save scene/template choices and the effective renderer in a report.
3. **Local Remotion renderer.** Pin matching Remotion package versions with a lockfile. Build original hook, focus, concept-flow and takeaway templates. Reserve caption and platform-control space, animate only from frame time, and render sequentially with one browser and one rendering worker. Templates display supplied script content; do not invent benchmarks, prices or product demonstrations.
4. **Existing final assembly.** Feed silent motion clips into the established FFmpeg assembly. Keep measured narration, phrase captions, stock labels, thumbnail generation, H.264/AAC encoding and quality validation. Do not regenerate the script or voice when changing visual modes.
5. **Failure handling.** Check local dependencies before rendering. Apply the user's configured Normal fallback only to renderer failures, record that fallback visibly, and never bypass final media checks. When fallback is disabled, fail the saved job with an actionable controlled diagnostic. No automatic upload during development previews.
6. **Verification and rollout.** Test mode validation, default persistence, frozen retries, frame coverage, Hybrid footage choice, fallback behavior and pipeline compatibility. Run real local renders in each mode, decode/inspect previews and representative frames, and run the Python suite plus JavaScript checks. Restart the idle dashboard and activate Hybrid after verification.

## Acceptance criteria

- All three modes are selectable for manual runs/previews; a saved default applies to scheduled runs.
- Old and resumed jobs use their recorded choice rather than a newly selected default.
- Full motion renders every scene; Hybrid retains available licensed B-roll between animated opening/closing scenes.
- Subtitles, stock labels, voice and source data remain accurate; templates add no unsupported factual data.
- Render reports distinguish requested/effective modes and explain any fallback without exposing credentials.
- Local previews pass the existing dimensions, codec, frame rate, duration, audio and caption checks.
- No GitHub push, cloud rendering or extra video upload is performed.

## Performance and licensing

Use one Chromium instance and concurrency 1 by default on this machine. Cache bundles by source/dependency hash and scene clips by content/configuration hash, with successful-output checks before reuse. Large effects, remote fonts and 3D assets are outside this initial scope. Local rendering still consumes CPU, RAM and disk. Remotion's individual/small-team Free License includes automation and commercial video use; larger organizations must check their eligibility: https://www.remotion.dev/docs/license/faq.

## Verified implementation (21 October 2026)

All delivery steps are implemented. The dashboard has three rendering modes and separate Groq/Gemini script-provider selection. Hybrid is the saved local default, Groq remains selected for scripts, and Kokoro Michael remains selected for English voice. No automatic provider change was made.

Normal, full Motion Graphics and Hybrid comparison previews passed real FFmpeg media validation. The final Hybrid preview also passed at 1080×1920 and 30 fps. A port conflict with another local app was fixed by selecting a free rendering port; a mixed MP4 timebase issue was fixed by remuxing motion clips before final assembly.

The full regression run passed 334 tests; the additional focused media run passed 20 tests, including the subsequently added check that full motion performs no stock downloads. Changed/new Python files pass Ruff, both JavaScript entry points pass syntax checks, and the dashboard controls were inspected in the browser. Pre-existing repository-wide lint findings in scheduling.py and test_upload_recovery.py were left outside this feature.

Preview artifacts are under output/motion-previews/ and remain ignored by Git. The implementation, lockfile and documentation are local changes only; no commit, GitHub push, cloud render or additional YouTube upload was performed.
