"""Content rights verification layer.

IMPORTANT: Processing of any podcast content is BLOCKED unless a RightsRecord
with status=AUTHORIZED exists.  This is a hard gate — no exceptions.

Usage:
    checker = RightsChecker(store)
    can_proceed, reason = checker.check_before_processing(episode_id)
    if not can_proceed:
        raise RuntimeError(reason)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from .blacklist import is_channel_blocked

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rights status enum
# ---------------------------------------------------------------------------

class RightsStatus(str, Enum):
    UNKNOWN = "unknown"
    AUTHORIZED = "authorized"         # Explicitly authorised by the user
    BLOCKED = "blocked"               # Explicitly blocked
    PENDING_REVIEW = "pending_review" # Awaiting manual verification


# ---------------------------------------------------------------------------
# Rights record
# ---------------------------------------------------------------------------

@dataclass
class RightsRecord:
    """Authorisation record for a single podcast episode."""

    episode_id: str
    status: RightsStatus
    basis: Optional[str]        # "creator_permission" | "cc_license" | "public_domain"
                                # | "fair_use_claimed" | "other"
    evidence_url: Optional[str] # Link to permission email / license page
    notes: Optional[str]        # Free-form notes
    verified_by: str            # "user_manual" | "automated_check"
    verified_at: datetime

    @property
    def can_process(self) -> bool:
        """True only when explicitly authorised."""
        return self.status == RightsStatus.AUTHORIZED

    @property
    def can_publish(self) -> bool:
        """True only when explicitly authorised."""
        return self.status == RightsStatus.AUTHORIZED

    def to_dict(self) -> dict:
        return {
            "episode_id": self.episode_id,
            "status": self.status.value,
            "basis": self.basis,
            "evidence_url": self.evidence_url,
            "notes": self.notes,
            "verified_by": self.verified_by,
            "verified_at": self.verified_at.isoformat(),
            "can_process": self.can_process,
            "can_publish": self.can_publish,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RightsRecord":
        d = dict(data)
        d["status"] = RightsStatus(d.get("status", "unknown"))
        raw_dt = d.get("verified_at")
        if isinstance(raw_dt, str):
            try:
                d["verified_at"] = datetime.fromisoformat(raw_dt)
            except ValueError:
                d["verified_at"] = datetime.now(timezone.utc)
        elif not isinstance(raw_dt, datetime):
            d["verified_at"] = datetime.now(timezone.utc)
        # Remove derived properties that are not constructor args
        d.pop("can_process", None)
        d.pop("can_publish", None)
        return cls(**d)


# ---------------------------------------------------------------------------
# Valid basis values
# ---------------------------------------------------------------------------

VALID_BASIS = {
    "creator_permission",
    "cc_license",
    "public_domain",
    "fair_use_claimed",
    "other",
}


# ---------------------------------------------------------------------------
# Rights checker
# ---------------------------------------------------------------------------

class RightsChecker:
    """Gate-keeper for podcast content processing.

    All operations that consume podcast audio/video MUST call
    ``check_before_processing`` first.  It raises ``PermissionError``
    when processing is not authorised.
    """

    def __init__(self, store):
        """
        Args:
            store: Store instance with save/get_podcast_episode methods.
        """
        self.store = store

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_rights(self, episode_id: str) -> Optional[RightsRecord]:
        """Look up the rights record for an episode from the store.

        Returns None if no record exists.
        """
        ep = self.store.get_podcast_episode(episode_id)
        if ep is None:
            return None
        rights_status = ep.get("rights_status", "unknown")
        if rights_status == "unknown":
            return None
        return RightsRecord(
            episode_id=episode_id,
            status=RightsStatus(rights_status),
            basis=ep.get("rights_basis"),
            evidence_url=None,  # Not persisted in episode table; see rights_notes
            notes=ep.get("rights_notes"),
            verified_by="user_manual",
            verified_at=datetime.fromisoformat(ep["rights_verified_at"])
            if ep.get("rights_verified_at")
            else datetime.now(timezone.utc),
        )

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def grant_authorization(
        self,
        episode_id: str,
        basis: str,
        evidence_url: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> RightsRecord:
        """Grant processing authorisation for an episode.

        Args:
            episode_id: The episode ID (source_id:video_id).
            basis: One of VALID_BASIS values.
            evidence_url: Optional URL to permission/license evidence.
            notes: Optional free-form notes.

        Returns:
            The created RightsRecord.

        Raises:
            ValueError: If the basis is not a recognised value.
        """
        if basis not in VALID_BASIS:
            raise ValueError(
                f"Unknown basis '{basis}'. Must be one of: {', '.join(sorted(VALID_BASIS))}"
            )

        ep = self.store.get_podcast_episode(episode_id)
        if ep:
            if is_channel_blocked(
                source_id=ep.get("source_id"),
                channel_name=ep.get("channel_name"),
                title=ep.get("title"),
            ):
                raise PermissionError(
                    f"Episode {episode_id} is from a blacklisted channel ({ep.get('channel_name') or ep.get('source_id')}). Authorization denied."
                )

        now = datetime.now(timezone.utc)
        record = RightsRecord(
            episode_id=episode_id,
            status=RightsStatus.AUTHORIZED,
            basis=basis,
            evidence_url=evidence_url,
            notes=notes,
            verified_by="user_manual",
            verified_at=now,
        )
        self._persist(record)
        log.info(
            "Rights AUTHORIZED for episode %s (basis=%s, by=user_manual)",
            episode_id, basis,
        )
        return record

    def block_episode(self, episode_id: str, reason: str) -> RightsRecord:
        """Mark an episode as explicitly blocked from processing.

        Args:
            episode_id: The episode ID.
            reason: Human-readable reason for blocking.

        Returns:
            The created RightsRecord.
        """
        now = datetime.now(timezone.utc)
        record = RightsRecord(
            episode_id=episode_id,
            status=RightsStatus.BLOCKED,
            basis=None,
            evidence_url=None,
            notes=reason,
            verified_by="user_manual",
            verified_at=now,
        )
        self._persist(record)
        log.warning("Rights BLOCKED for episode %s: %s", episode_id, reason)
        return record

    def mark_pending(self, episode_id: str, notes: Optional[str] = None) -> RightsRecord:
        """Mark an episode as pending manual review.

        Args:
            episode_id: The episode ID.
            notes: Optional notes about the pending review.

        Returns:
            The created RightsRecord.
        """
        now = datetime.now(timezone.utc)
        record = RightsRecord(
            episode_id=episode_id,
            status=RightsStatus.PENDING_REVIEW,
            basis=None,
            evidence_url=None,
            notes=notes,
            verified_by="user_manual",
            verified_at=now,
        )
        self._persist(record)
        log.info("Rights PENDING_REVIEW for episode %s", episode_id)
        return record

    # ------------------------------------------------------------------
    # Gate check
    # ------------------------------------------------------------------

    def check_before_processing(self, episode_id: str) -> tuple[bool, str]:
        """Hard gate: return (can_proceed, reason_message).

        NEVER allows processing without an AUTHORIZED record.

        Args:
            episode_id: The episode ID to check.

        Returns:
            Tuple of (True, "Authorised") when processing is permitted, or
            (False, reason_string) when blocked.

        Raises:
            PermissionError: When explicitly blocked (status == BLOCKED).
        """
        ep = self.store.get_podcast_episode(episode_id)
        if ep:
            if is_channel_blocked(
                source_id=ep.get("source_id"),
                channel_name=ep.get("channel_name"),
                title=ep.get("title"),
            ):
                return (
                    False,
                    f"Episode {episode_id} is from a blacklisted channel ({ep.get('channel_name') or ep.get('source_id')}). Processing blocked.",
                )

        record = self.get_rights(episode_id)

        if record is None:
            return (
                False,
                f"Episode {episode_id} has no rights record. "
                "Run 'autoyt podcast authorize' to grant explicit authorisation "
                "before processing. No content rights verification = no processing.",
            )

        if record.status == RightsStatus.AUTHORIZED:
            return True, "Authorised"

        if record.status == RightsStatus.BLOCKED:
            raise PermissionError(
                f"Episode {episode_id} is explicitly BLOCKED from processing. "
                f"Reason: {record.notes or 'no reason recorded'}. "
                "To unblock, re-run 'autoyt podcast authorize' with a valid basis."
            )

        if record.status == RightsStatus.PENDING_REVIEW:
            return (
                False,
                f"Episode {episode_id} is PENDING_REVIEW. "
                "Complete manual rights verification and run 'autoyt podcast authorize' "
                "to grant explicit authorisation.",
            )

        # UNKNOWN or any future status
        return (
            False,
            f"Episode {episode_id} has unknown rights status '{record.status}'. "
            "Explicit authorisation is required before processing.",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _persist(self, record: RightsRecord) -> None:
        """Write rights data back into the podcast_episodes table."""
        ep = self.store.get_podcast_episode(record.episode_id)
        if ep is None:
            # Create a minimal stub so we can record rights even before discovery
            self.store.save_podcast_episode({
                "id": record.episode_id,
                "source_id": record.episode_id.split(":")[0] if ":" in record.episode_id else "unknown",
                "video_id": record.episode_id.split(":")[-1],
                "video_url": "",
                "title": "",
                "channel_name": "",
                "published_at": datetime.now(timezone.utc).isoformat(),
                "status": "rights_pending",
                "rights_status": record.status.value,
                "rights_basis": record.basis,
                "rights_notes": record.notes,
                "rights_verified_at": record.verified_at.isoformat(),
                "discovered_at": datetime.now(timezone.utc).isoformat(),
            })
        else:
            new_status = (
                "authorized" if record.status == RightsStatus.AUTHORIZED
                else ("skipped" if record.status == RightsStatus.BLOCKED else ep.get("status", "discovered"))
            )
            self.store.update_episode_status(
                record.episode_id,
                new_status,
                rights_status=record.status.value,
                rights_basis=record.basis,
                rights_notes=record.notes,
                rights_verified_at=record.verified_at.isoformat(),
            )
