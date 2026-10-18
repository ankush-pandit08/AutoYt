"""Configured visibility, persistent resumable uploads and conservative outcome handling."""
import json
import os
import random
import time

from .store import now

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def service(cfg, interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build

    token = cfg.youtube_token_file
    credentials = None
    if token.is_file():
        credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        try:
            credentials.refresh(Request())
        except Exception:
            raise RuntimeError("CONFIGURATION: Google consent revoked or expired; run autoyt auth") from None
    if not credentials or not credentials.valid:
        if not interactive:
            raise RuntimeError("CONFIGURATION: initial OAuth consent required; run autoyt auth")
        if not cfg.youtube_client_secrets_file.is_file():
            raise RuntimeError("CONFIGURATION: Google desktop OAuth client JSON is missing; see SETUP.md")
        credentials = InstalledAppFlow.from_client_secrets_file(
            str(cfg.youtube_client_secrets_file), SCOPES).run_local_server(port=0)
    token.parent.mkdir(parents=True, exist_ok=True)
    temporary = token.with_suffix(".tmp")
    temporary.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(token)
    youtube = build("youtube", "v3", credentials=credentials, cache_discovery=False)
    channels = youtube.channels().list(part="id,contentDetails", mine=True).execute().get("items", [])
    if len(channels) != 1:
        raise RuntimeError("CONFIGURATION: authorize exactly one YouTube channel")
    channel = channels[0]
    if cfg.youtube_channel_id and channel["id"] != cfg.youtube_channel_id:
        raise RuntimeError("CONFIGURATION: OAuth channel does not match YOUTUBE_CHANNEL_ID")
    if not interactive and not cfg.youtube_channel_id:
        raise RuntimeError("CONFIGURATION: pin YOUTUBE_CHANNEL_ID before use")
    return youtube, channel


def metadata(script, job_id, assets, privacy="private"):
    if privacy not in ("private", "public"):
        raise ValueError("Unsupported upload visibility")
    language = "hi" if any("\u0900" <= c <= "\u097f" for c in script.script) else "en"
    description = script.description + "\n\n" + " ".join(script.hashtags)
    description += "\n\nSources:\n" + "\n".join(script.source_urls)
    description += "\n\nVisuals are illustrative.\n" + "\n".join(sorted({a["attribution"] for a in assets}))
    description += f"\n\nAutoYT job: {job_id}"
    if len(description) > 4900 or len(description.encode("utf-8")) > 4900:
        raise ValueError("YouTube description exceeds safe length")
    if any(c in script.title for c in "<>"):
        raise ValueError("Invalid YouTube title characters")
    return {"snippet": {"title": script.title, "description": description,
                        "tags": [h.lstrip("#") for h in script.hashtags], "categoryId": "28",
                        "defaultLanguage": language, "defaultAudioLanguage": language},
            "status": {"privacyStatus": privacy,
                       "containsSyntheticMedia": script.needs_synthetic_media_disclosure}}


def reconcile(youtube, channel, job_id):
    playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    page = None
    for _ in range(3):
        response = youtube.playlistItems().list(part="snippet,contentDetails", playlistId=playlist,
                                               maxResults=50, pageToken=page).execute()
        for item in response.get("items", []):
            if f"AutoYT job: {job_id}" in item["snippet"].get("description", ""):
                return item["contentDetails"]["videoId"]
        page = response.get("nextPageToken")
        if not page:
            break
    return None


def _confirm_once(youtube, store, job_id, video_id):
    expected = store.load(job_id, "metadata")["status"]["privacyStatus"]
    if expected not in ("private", "public"):
        raise ValueError("Unsupported saved upload visibility")
    response = youtube.videos().list(part="status,processingDetails", id=video_id).execute()
    items = response.get("items", [])
    if not items:
        raise RuntimeError("TRANSIENT: uploaded video not yet visible to API; retry to verify")
    status = items[0]["status"]
    if status.get("privacyStatus") != expected:
        raise RuntimeError("PERMANENT: API did not confirm requested " + expected + " visibility")
    if status.get("uploadStatus") in ("failed", "rejected", "deleted"):
        raise RuntimeError("PERMANENT: YouTube processing failed or rejected the video")
    processing = items[0].get("processingDetails", {}).get("processingStatus")
    if processing in ("failed", "terminated"):
        raise RuntimeError("PERMANENT: YouTube processing failed")
    if (processing is not None and processing != "succeeded") or (
            processing is None and status.get("uploadStatus") != "processed"):
        raise RuntimeError("TRANSIENT: YouTube is processing the video; retry to verify")
    store.confirm_upload(job_id, video_id, expected)
    store.upload_progress(job_id, "CONFIRMED", video_id=video_id)
    store.update(job_id, "PUBLISHED" if expected == "public" else "AWAITING_REVIEW", error=None)
    store.save(job_id, "youtube", {"video_id": video_id, "url": f"https://youtu.be/{video_id}",
                                   "privacy": expected, "uploaded_at": now(),
                                   "thumbnail_applied": False})
    return video_id


def confirm(youtube, store, job_id, video_id):
    for attempt in range(6):
        try:
            return _confirm_once(youtube, store, job_id, video_id)
        except RuntimeError as error:
            if not str(error).startswith("TRANSIENT:") or attempt == 5:
                raise
            time.sleep(10)


def upload(cfg, store, job_id, folder):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not cfg.enable_upload:
        raise RuntimeError("CONFIGURATION: ENABLE_UPLOAD=false; authenticate, then explicitly enable")
    job = store.get(job_id)
    if job["dry_run"]:
        raise ValueError("Dry-run jobs cannot upload; generate a new non-dry-run job")
    validation = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
    if not validation.get("passed"):
        raise ValueError("Video has not passed validation")
    from .store import digest
    if job["content_hash"] != digest(folder / "final.mp4"):
        raise ValueError("Video changed after validation")
    youtube, channel = service(cfg)
    attempt = store.attempt(job_id)
    if attempt and attempt["video_id"]:
        return confirm(youtube, store, job_id, attempt["video_id"])
    if attempt and not attempt["session_uri"]:
        video_id = reconcile(youtube, channel, job_id)
        if video_id:
            store.upload_progress(job_id, "RETURNED_ID", video_id=video_id)
            return confirm(youtube, store, job_id, video_id)
        raise RuntimeError("PERMANENT: ambiguous upload initiation; no duplicate insert allowed. "
                           "Review YouTube Studio and this job's upload_attempts record")
    body = store.load(job_id, "metadata")
    # Freeze visibility per job, so changing defaults never republishes an old private video on retry.
    privacy = body["status"]["privacyStatus"]
    if privacy not in ("private", "public"):
        raise ValueError("Unsupported saved upload visibility")
    body["status"] = {"privacyStatus": privacy, "selfDeclaredMadeForKids": cfg.youtube_made_for_kids,
                      "containsSyntheticMedia": body["status"].get("containsSyntheticMedia", False)}
    media = MediaFileUpload(str(folder / "final.mp4"), mimetype="video/mp4", chunksize=4*1024*1024,
                            resumable=True)
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)
    if attempt:
        request.resumable_uri = attempt["session_uri"]
        request.resumable_progress = attempt["progress"]
        request._in_error_state = True  # Official client's next_chunk queries server progress first.
    else:
        store.upload_progress(job_id, "INITIATING")
    store.update(job_id, "UPLOADING")
    failures = 0
    while True:
        try:
            _, response = request.next_chunk(num_retries=0)
            store.upload_progress(job_id, "SESSION", request.resumable_uri, request.resumable_progress)
            if response:
                video_id = response["id"]
                # Persist returned ID before any further network request.
                store.upload_progress(job_id, "RETURNED_ID", video_id=video_id)
                return confirm(youtube, store, job_id, video_id)
            failures = 0
        except Exception as error:
            if (store.attempt(job_id) or {}).get("video_id"):
                raise
            store.upload_progress(job_id, "INTERRUPTED", request.resumable_uri, request.resumable_progress)
            retryable = not isinstance(error, HttpError) or error.resp.status in (429, 500, 502, 503, 504)
            if not retryable or failures >= 3:
                status = str(error.resp.status) if isinstance(error, HttpError) else type(error).__name__
                raise RuntimeError("Upload paused (" + status + "); session saved. Retry this job, "
                                   "never create a replacement upload") from None
            request._in_error_state = True
            time.sleep(min(2 ** failures + random.random(), 15))
            failures += 1
