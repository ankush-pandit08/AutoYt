# Personal control room

Windows: run `powershell -ExecutionPolicy Bypass -File scripts/start-autoyt.ps1` from this project. Linux: run `python -m autoyt dashboard` inside its virtual environment. Open **http://127.0.0.1:8765**. There is no registration, login, paid service or cloud hosting. Dashboard assets are bundled locally; there are no CDNs or analytics.

The dashboard includes its scheduler. Do not run a separate `scheduler` process alongside it. Closing the browser does not stop AutoYT. Stopping the Python process does. The machine must stay awake, connected to the internet and able to reach local Ollama. Windows sign-in startup is available through `scripts/enable-autostart.ps1`; it requires sign-in and does not bypass Windows security.

- **Auto mode:** pause/resume future slots. The saved state survives restart. Work already underway can finish, including its configured upload.
- **Daily results:** confirmed uploads, new live attempts and failed live attempts in the configured timezone. Private and public confirmations both count as uploaded. Manual runs and dry-run previews do not consume the automatic daily attempt budget. Retries resume an existing job and are not new attempts.
- **Live pipeline:** the current stage and completed stages. If AutoYT was interrupted, the saved job is recovered on the next run; a stale stage alone does not prove a process is running.
- **Video library:** filter runs, open actual local MP4 previews, inspect scripts, evidence sources, errors and per-asset license/creator records. Only completed YouTube confirmations get an upload link.
- **Generate preview:** runs the pipeline with uploads permanently disabled for that job.
- **Run now:** starts a live run with configured visibility and no app daily cap. Provider quotas and quality checks still apply. The UI clearly confirms public publication before a manual run.
- **Retry:** resumes a failed job's checkpoint, keeping its saved visibility. Missing evidence, authorization or bad model output may fail again; retry does not weaken checks.

Ten slots are scheduled from 06:00 to 21:00 Asia/Kolkata. Edit `SCHEDULE_TIMES` and `MAX_VIDEOS_PER_DAY` in `.env` and restart the process to change them. One to ten distinct HH:MM slots are supported. Runs are serialized and old missed slots are skipped; failures can yield fewer than ten uploads. No background backlog tries to compensate by flooding the channel.

For a server, see [SERVER.md](SERVER.md). Keep the loopback binding and access it through SSH forwarding. Do not expose port 8765 to the internet. The UI deliberately shows no keys, OAuth tokens or resumable session URIs. New OAuth consent still uses `python -m autoyt auth`.

Unattended writing remains unvalidated on the tested low-memory machine. The dashboard reports failures; the existence of a scheduler does not establish that ten verified stories can be produced every day.

Research failures show per-source outcomes in run details, including blocked HTTP responses and stale or undated pages. Discovery checks up to twelve candidate stories per live run. New feeds broaden coverage, while publication still requires recent official and independent corroboration. Some days have too few verifiable fresh stories to publish.


## Reconnect YouTube

In **Production settings → YouTube connection**, click **Reconnect YouTube**. Sign in with Google in the new tab, choose the already configured channel, approve the existing upload/read permissions, and return to AutoYT. If a popup is blocked, use **Continue with Google**. The app verifies the channel before atomically replacing the saved token; cancellation or the wrong channel keeps the old token. Reconnecting never starts a video or upload. After success, retry the saved failed job to resume its upload. Requests expire after ten minutes; Cancel connection allows starting over.

Initial setup still needs the Google desktop OAuth client JSON and a pinned `YOUTUBE_CHANNEL_ID`. Expired access tokens normally refresh automatically; a rejected refresh permission is shown as **Reconnect required**. The button stores the new authorization only in the app instance where it is used. Use localhost on that machine, or forward the same dashboard port over SSH if accessing an existing remote instance. This change does not expose the localhost dashboard publicly or transfer credentials between machines.

Creative visuals and sound: see [creative asset setup](docs/creative-assets.md) for the Video style switches and LottieFiles/Freesound import workflow.

## Voiceover duration repair

Automatically generated narration is measured at the selected voice's normal speed. If it falls outside the configured duration limits (currently 35–55 seconds), the app uses the measured words per second and previous draft to expand or shorten the script toward the middle of the range. Up to three source-checked revisions are allowed, with a word-count check before new speech requests. The app saves `narration_duration_attempts.json` in the run folder for diagnosis. It never time-stretches the voice or pads narration with silence. User-supplied narration and explicit `script_revision.json` files remain unchanged and require editing if out of range.
