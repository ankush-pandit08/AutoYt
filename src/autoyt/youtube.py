"""Configured visibility, persistent resumable uploads and conservative outcome handling."""
import json
import logging
import os
import random
import time

from .store import now

log = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.readonly"]


def creator_topics(cfg, channel_ids):
    """Read bounded public upload metadata; creator videos are leads, never claim evidence."""
    from datetime import datetime, timezone

    from googleapiclient.errors import HttpError

    from .discovery import MAX_STORY_AGE_SECONDS, TECH, words
    from .models import Topic

    youtube, _ = service(cfg)
    channels = youtube.channels().list(part="contentDetails", id=",".join(channel_ids)).execute()
    result = []
    for channel in channels.get("items", []):
        playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        try:
            uploads = youtube.playlistItems().list(part="snippet,contentDetails", playlistId=playlist,
                                                   maxResults=10).execute()
        except HttpError as error:
            if error.resp.status in (403, 404):
                log.info("A creator upload playlist is unavailable; continuing other references")
                continue
            raise
        for item in uploads.get("items", []):
            snippet, detail = item["snippet"], item["contentDetails"]
            published = detail.get("videoPublishedAt")
            if not published or not words(snippet["title"]) & TECH:
                continue
            date = datetime.fromisoformat(published.replace("Z", "+00:00"))
            if not 0 <= (datetime.now(timezone.utc)-date).total_seconds() <= MAX_STORY_AGE_SECONDS:
                continue
            result.append(Topic(title=snippet["title"], url="https://www.youtube.com/watch?v="+detail["videoId"],
                                published_at=date, source="https://www.youtube.com/channel/"+channel["id"]))
    return result


def service(cfg, interactive=False):
    from google.auth.exceptions import RefreshError, TransportError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_httplib2 import AuthorizedHttp
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import build_http

    token = cfg.youtube_token_file
    credentials = None
    if token.is_file():
        credentials = Credentials.from_authorized_user_file(str(token), SCOPES)
    if credentials and credentials.expired and credentials.refresh_token:
        for attempt in range(3):
            try:
                credentials.refresh(Request())
                break
            except RefreshError as error:
                codes = [value.get("error") for value in error.args if isinstance(value, dict)]
                if "invalid_grant" in codes:
                    if interactive:
                        credentials = None
                        break
                    raise RuntimeError("CONFIGURATION: Google consent revoked or expired; run autoyt auth") from None
                if not getattr(error, "retryable", False):
                    raise RuntimeError("CONFIGURATION: Google token refresh rejected; check desktop OAuth client setup") from None
                if attempt == 2:
                    raise RuntimeError("TRANSIENT: Google authorization service unavailable; retry this saved job") from None
            except TransportError:
                if attempt == 2:
                    raise RuntimeError("TRANSIENT: Cannot reach Google to refresh authorization; "
                                       "check internet and retry this saved job") from None
            except Exception:
                raise RuntimeError("CONFIGURATION: Google token refresh failed; check local OAuth configuration") from None
            time.sleep(2 ** attempt)
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
    transport = build_http()  # Preserves 308 as resumable progress, rather than an HTTP redirect.
    transport.timeout = 300
    youtube = build("youtube", "v3", http=AuthorizedHttp(credentials, http=transport),
                    cache_discovery=False)
    channels = youtube.channels().list(part="id,contentDetails", mine=True).execute().get("items", [])
    if len(channels) != 1:
        raise RuntimeError("CONFIGURATION: authorize exactly one YouTube channel")
    channel = channels[0]
    if cfg.youtube_channel_id and channel["id"] != cfg.youtube_channel_id:
        raise RuntimeError("CONFIGURATION: OAuth channel does not match YOUTUBE_CHANNEL_ID")
    if not interactive and not cfg.youtube_channel_id:
        raise RuntimeError("CONFIGURATION: pin YOUTUBE_CHANNEL_ID before use")
    return youtube, channel


def metadata(script, job_id, assets, privacy="private", include_description=False):
    if privacy not in ("private", "public"):
        raise ValueError("Unsupported upload visibility")
    language = "hi" if any("\u0900" <= c <= "\u097f" for c in script.script) else "en"
    description = script.description + "\n\n" + " ".join(script.hashtags)
    description += "\n\nSources:\n" + "\n".join(script.source_urls)
    credits = set()
    for asset in assets:
        credit = asset["attribution"]
        if asset.get("license") == "CC-BY-4.0":
            credit += " | " + asset["license_url"] + " | Edited/cropped for this video"
            credit += " | " + asset["source_url"]
        credits.add(credit)
    description += "\n\nVisuals are illustrative.\n" + "\n".join(sorted(credits))
    description += f"\n\nAutoYT job: {job_id}"
    if not include_description:
        description = ""
    if len(description) > 4900 or len(description.encode("utf-8")) > 4900:
        raise ValueError("YouTube description exceeds safe length")
    if any(c in script.title for c in "<>"):
        raise ValueError("Invalid YouTube title characters")
    return {"snippet": {"title": script.title, "description": description,
                        "tags": [h.lstrip("#") for h in script.hashtags] + [f"AutoYT-job-{job_id}"], "categoryId": "28",
                        "defaultLanguage": language, "defaultAudioLanguage": language},
            "status": {"privacyStatus": privacy,
                       "containsSyntheticMedia": script.needs_synthetic_media_disclosure}}


def reconcile(youtube, channel, job_id, excluded_ids=()):
    playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
    page = None
    for _ in range(3):
        response = youtube.playlistItems().list(part="snippet,contentDetails", playlistId=playlist,
                                               maxResults=50, pageToken=page).execute()
        candidates = []
        for item in response.get("items", []):
            if item["contentDetails"]["videoId"] in excluded_ids:
                continue
            if f"AutoYT job: {job_id}" in item["snippet"].get("description", ""):
                return item["contentDetails"]["videoId"]
            candidates.append(item["contentDetails"]["videoId"])
        # A metadata tag preserves recovery when the public description is empty.
        if candidates:
            videos = youtube.videos().list(part="snippet", id=",".join(candidates)).execute()
            for video in videos.get("items", []):
                if f"AutoYT-job-{job_id}" in video.get("snippet", {}).get("tags", []):
                    return video["id"]
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


class SessionTransport:
    """Persist the Location header before the official client sends any video bytes."""
    def __init__(self, inner, store, job_id):
        self.inner, self.store, self.job_id = inner, store, job_id

    def request(self, uri, method="GET", *args, **kwargs):
        response, content = self.inner.request(uri, method, *args, **kwargs)
        if method == "POST" and response.status in (200, 201) and response.get("location"):
            self.store.upload_progress(self.job_id, "SESSION", response["location"], 0)
        return response, content


def upload_error_reason(error):
    try:
        reasons = {item.get("reason") for item in json.loads(error.content).get("error", {}).get("errors", [])}
    except (ValueError, TypeError, AttributeError):
        reasons = set()
    messages = {
        "uploadLimitExceeded": "YouTube channel upload limit reached; wait before retrying this saved job",
        "quotaExceeded": "YouTube API quota exhausted; retry after the quota resets",
        "dailyLimitExceeded": "YouTube API daily limit reached; retry after reset",
        "invalidTitle": "YouTube rejected the video title",
        "invalidDescription": "YouTube rejected the description",
        "invalidTags": "YouTube rejected the video tags",
        "invalidVideoMetadata": "YouTube rejected the video metadata",
        "forbidden": "YouTube denied upload permission; check the channel and OAuth access",
        "insufficientPermissions": "YouTube OAuth permissions do not permit uploads; reauthorize the channel",
    }
    for reason, message in messages.items():
        if reason in reasons:
            return reason, message
    return "httpError", "YouTube rejected the upload request"


def upload(cfg, store, job_id, folder):
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    if not cfg.enable_upload:
        raise RuntimeError("CONFIGURATION: ENABLE_UPLOAD=false; authenticate, then explicitly enable")
    if (store.load(job_id, "upload_block") or {}).get("blocked"):
        raise RuntimeError("CONFIGURATION: this job has a cancelled upload requiring reconciliation before replacement")
    job = store.get(job_id)
    if job["dry_run"]:
        raise ValueError("Dry-run jobs cannot upload; generate a new non-dry-run job")
    attempt = store.attempt(job_id)
    youtube = channel = None
    # Reconcile accepted/uncertain outcomes even after retention has removed local media.
    if attempt and (attempt["video_id"] or (not attempt["session_uri"] and attempt["state"] != "REJECTED")):
        youtube, channel = service(cfg)
        video_id = attempt["video_id"] or reconcile(youtube, channel, job_id,
            [a["video_id"] for a in (store.load(job_id, "cancelled_uploads") or []) if a.get("video_id")])
        if video_id:
            store.upload_progress(job_id, "RETURNED_ID", video_id=video_id)
            return confirm(youtube, store, job_id, video_id)
        raise RuntimeError("PERMANENT: ambiguous upload initiation; no duplicate insert allowed. "
                           "Review YouTube Studio and this job's upload_attempts record")
    validation = json.loads((folder / "validation.json").read_text(encoding="utf-8"))
    if not validation.get("passed"):
        raise ValueError("Video has not passed validation")
    from .store import digest
    if job["content_hash"] != digest(folder / "final.mp4"):
        raise ValueError("Video changed after validation")
    from .licensing import audit
    assets = store.load(job_id, "media_assets")
    license_audit = audit(assets)
    description = store.load(job_id, "metadata")["snippet"]["description"]
    for asset in assets:
        if asset.get("license") == "CC-BY-4.0" and any(
                value not in description for value in (asset["attribution"], asset["license_url"], asset["source_url"])):
            raise ValueError("CC BY media requires creator, source and license credits in the upload description")
    store.save(job_id, "media_license_audit", license_audit)
    attempt = store.attempt(job_id)
    if job["slot"] and not attempt and store.daily_upload_count(
            cfg.timezone, scheduled_only=True) >= cfg.max_videos_per_day:
        raise RuntimeError("CONFIGURATION: daily scheduled upload limit reached; retry this saved job another day")
    if youtube is None:
        youtube, channel = service(cfg)
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
    if hasattr(request, "http"):
        request.http = SessionTransport(request.http, store, job_id)
    if attempt and attempt["session_uri"]:
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
            log.info("Job %s upload progress: %s bytes", job_id, request.resumable_progress)
            if response:
                video_id = response["id"]
                # Persist returned ID before any further network request.
                store.upload_progress(job_id, "RETURNED_ID", video_id=video_id)
                return confirm(youtube, store, job_id, video_id)
            failures = 0
        except Exception as error:
            if (store.attempt(job_id) or {}).get("video_id"):
                raise
            session = request.resumable_uri or (store.attempt(job_id) or {}).get("session_uri")
            request.resumable_uri = session
            rejected = isinstance(error, HttpError) and 400 <= error.resp.status < 500 and error.resp.status != 408 and (error.resp.status != 429 or not session)
            store.upload_progress(job_id, "REJECTED" if rejected and not session else "INTERRUPTED",
                                  session, request.resumable_progress)
            if rejected:
                reason, message = upload_error_reason(error)
                store.save(job_id, "upload_error", {"http_status": error.resp.status, "reason": reason})
                suffix = "; session saved. Retry this job" if session else "; no video accepted. Retry this saved job"
                raise RuntimeError("CONFIGURATION: " + message + suffix) from None
            if not session:
                raise RuntimeError("PERMANENT: ambiguous upload initiation; no duplicate insert allowed. "
                                   "Retry this job to reconcile YouTube before another insert") from None
            retryable = not isinstance(error, HttpError) or error.resp.status in (429, 500, 502, 503, 504)
            if not retryable or failures >= 3:
                status = str(error.resp.status) if isinstance(error, HttpError) else type(error).__name__
                category = "TRANSIENT" if retryable else "CONFIGURATION"
                raise RuntimeError(category + ": Upload paused (" + status + "); session saved. Retry this job, "
                                   "never create a replacement upload") from None
            request._in_error_state = True
            time.sleep(min(2 ** failures + random.random(), 15))
            failures += 1
