"""YouTube Upload Integration for Approved Podcast Clips.

Enforces:
1. Episode rights MUST be AUTHORIZED.
2. Clip status MUST be APPROVED by user.
3. Default upload visibility is PRIVATE (review mode).
"""
from __future__ import annotations

import logging
from pathlib import Path

from ..youtube import service
from .rights import RightsChecker

log = logging.getLogger(__name__)


class PodcastPublisher:
    """Uploads approved podcast clips to YouTube in review/private mode."""

    def __init__(self, cfg, store):
        self.cfg = cfg
        self.store = store
        self.rights_checker = RightsChecker(store)

    def upload_clip(self, clip_id: str, privacy: str = "private") -> dict:
        """Upload an approved clip to YouTube.

        Raises:
            PermissionError: If rights are not authorized or clip is not approved.
            ValueError: If clip is missing or video file not found.
        """
        clip = self.store.get_podcast_clip(clip_id)
        if not clip:
            raise ValueError(f"Clip {clip_id} not found in database")

        episode_id = clip["episode_id"]

        # Hard Gate 1: Check rights
        can_proceed, reason = self.rights_checker.check_before_processing(episode_id)
        if not can_proceed:
            raise PermissionError(f"Upload blocked by rights layer: {reason}")

        # Hard Gate 2: Check clip approval
        if clip.get("status") != "approved":
            raise PermissionError(
                f"Clip {clip_id} has status '{clip.get('status')}'. "
                "Only approved clips can be uploaded."
            )

        video_path = Path(clip["output_path"]) if clip.get("output_path") else None
        if not video_path or not video_path.is_file():
            raise ValueError(f"Rendered video file missing for clip {clip_id}: {video_path}")

        thumb_path = Path(clip["thumbnail_path"]) if clip.get("thumbnail_path") else None

        title = clip.get("generated_title") or "Podcast Clip #Shorts"
        description = clip.get("generated_description") or ""
        tags = clip.get("hashtags") or ["Shorts", "Podcast"]

        log.info(
            "Uploading clip %s to YouTube (privacy=%s, title='%s')...",
            clip_id, privacy, title
        )

        # Upload via service(cfg)
        try:
            client, _ = service(self.cfg)
            upload_result = self._execute_upload(
                client=client,
                video_file=video_path,
                title=title,
                description=description,
                tags=tags,
                privacy=privacy,
            )
            video_id = upload_result.get("id")
            youtube_url = f"https://www.youtube.com/shorts/{video_id}"

            # Optionally upload custom thumbnail if supported
            if thumb_path and thumb_path.is_file() and video_id:
                try:
                    self._upload_thumbnail(client, video_id, thumb_path)
                except Exception as t_err:
                    log.warning("Thumbnail upload optional notice for %s: %s", video_id, t_err)

            # Update database
            self.store.update_clip_status(
                clip_id,
                "uploaded",
                youtube_video_id=video_id,
                youtube_url=youtube_url,
                upload_status="success",
            )
            log.info("Clip %s uploaded successfully! URL: %s", clip_id, youtube_url)
            return {
                "clip_id": clip_id,
                "video_id": video_id,
                "youtube_url": youtube_url,
                "status": "uploaded",
                "privacy": privacy,
            }

        except Exception as exc:
            log.error("Upload failed for clip %s: %s", clip_id, exc)
            self.store.update_clip_status(
                clip_id,
                "failed",
                upload_status=f"Failed: {exc}",
            )
            raise

    def _execute_upload(
        self, client, video_file: Path, title: str, description: str, tags: list[str], privacy: str
    ) -> dict:
        """Execute resumable upload with googleapiclient."""
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {
                "title": title[:100],
                "description": description[:5000],
                "tags": [t.lstrip("#") for t in tags][:15],
                "categoryId": "22",  # People & Blogs / Entertainment
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(
            str(video_file),
            mimetype="video/mp4",
            resumable=True,
            chunksize=1024 * 1024 * 5,  # 5MB chunks
        )

        request = client.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                log.info("Upload progress: %d%%", int(status.progress() * 100))

        return response

    def _upload_thumbnail(self, client, video_id: str, thumb_file: Path) -> None:
        """Set thumbnail for video."""
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(thumb_file), mimetype="image/png")
        client.thumbnails().set(videoId=video_id, media_body=media).execute()
        log.info("Custom thumbnail applied to video %s", video_id)
