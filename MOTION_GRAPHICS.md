# Local motion graphics

Choose **Normal**, **Motion Graphics** or **Hybrid** in the dashboard's **Video style** card. The selection is used for the next new manual run/preview. **Save as default** also applies it to scheduled runs and saves the Normal-fallback preference. A job records its rendering selection; changing defaults does not change a retry or old job. Jobs created before this feature use Normal.

Normal uses the existing image/footage renderer. Motion Graphics animates every scene with original React templates. Hybrid animates the opening, closing and scenes without footage; available licensed B-roll remains in the other scenes. If no suitable footage is available, Hybrid may consist entirely of motion graphics.

## Setup

Install Node.js (20 or newer), then run from the project root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup_motion.ps1
```

This installs the exact versions in the lockfile and downloads Remotion's headless Chromium browser. Dependencies are inside `src/autoyt/remotion/node_modules`, which is ignored by Git. The source and setup script are packaged with AutoYT. No cloud render service or rendering API key is needed.

Linux/macOS: run `npm ci` followed by `node render.mjs --setup` inside `src/autoyt/remotion`; standard Chromium system libraries may also be required. Source/wheel installations need that package directory to be writable during setup.

```dotenv
RENDER_MODE=hybrid
RENDER_FALLBACK=true
REMOTION_NODE=node
REMOTION_CONCURRENCY=1
REMOTION_TIMEOUT_SECONDS=1800
```

Use an absolute Node executable path if it is not on the launcher's PATH. Saved dashboard defaults in `data/database/render-settings.json` take precedence over environment defaults. Keep concurrency 1 on machines with limited memory.

## What is animated

Reusable hook, key-point, abstract concept-flow and takeaway templates use the saved scene headlines and narration. Timing follows measured narration boundaries, quantized cumulatively to frames to avoid drift. Rendering uses one browser and bounded concurrency; frame animations do not depend on random values, network calls or wall-clock time. Content is passed as JSON, never executed as generated React code.

Motion clips are silent. The established FFmpeg stage still assembles narration, bold captions, illustrative-stock labels, H.264/AAC output and thumbnails. The existing license and final media checks run before publication. Phrase caption times remain estimates within measured narration scenes. Templates cannot invent benchmark values, prices, code demonstrations or product specifications.

`render_report.json` records requested/effective modes, templates, scene-frame coverage and any fallback. The report appears in run details and `content_package.json`. Renderer failures use Normal only if fallback was enabled for that job. Invalid scene timing and final quality failures still stop publication. Partial bundle/clip outputs are not reused as successful cached renders.

## Preview without uploading or API calls

```powershell
.\.venv\Scripts\python.exe scripts/preview_motion.py --mode all
```

This uses the existing `output/voice-previews/kokoro-michael-english.wav` sample. Supply another audio file with `--audio`; the demonstration's text is fixed, so substitute matching demo audio for meaningful subtitle synchronization. `--assets-json path/to/media_assets.json` uses the first existing licensed image/clip as B-roll. Without it, the demonstration uses original graphics. `--width 1080` produces full-resolution previews; the default 540px width is quicker to compare locally. Files are written below `output/motion-previews/`, with validation reports. No video is uploaded.

## Script model selection

The separate **Script generation** card offers **Groq** and **Gemini** and displays their configured model IDs. Saving requires the corresponding API key to be present, but does not consume API credits or confirm remaining quota. Keys stay in the environment and never appear in saved preferences or API status responses. The saved provider applies to new research/script generation and morning planning. Each new job records its provider/model for consistent retries. Existing scripts are not rewritten when this setting changes, and voice selection remains separate. No automatic provider switching occurs.

## Licensing and costs

These original templates add no paid assets, remote fonts or music. Remotion's current Free License includes individual users and organizations/teams of up to three people, including commercial video use and automation. Other organizations must check their eligibility: https://www.remotion.dev/docs/license/faq. Local rendering still uses CPU, RAM, disk and electricity; script/voice providers retain their own pricing and quotas. Cloud services are not part of this implementation.
