"""Channel and Source Blacklist Management for AutoYT.

Prevents discovery, processing, and publishing of content from blocked channels.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

DEFAULT_BLOCKED_FILE = Path("data/blocked_channels.json")

_BUILTIN_BLOCKED: list[dict] = [
    {
        "id": "shubhankar_mishra",
        "name": "Unplugged Shubhankar",
        "handles": ["@unpluggedshubhankar", "@shubhankarmishraofficial"],
        "channel_ids": ["UC0Qb7WEWW3xBop_6LC4t7qQ", "UCYalWwW79BdxmMTKUyBFj5g"],
        "keywords": ["unplugged shubhankar", "shubhankar mishra"],
        "reason": "User requested removal - do not pick any video Shorts from this channel",
        "blocked_at": "2026-11-23T10:40:00+00:00",
    }
]


def load_blocked_channels(file_path: Optional[Path] = None) -> list[dict]:
    """Load the list of blocked channels from JSON, falling back to built-in blocked list."""
    target = file_path or DEFAULT_BLOCKED_FILE
    if not target.is_file():
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            data = {"version": 1, "blocked_channels": _BUILTIN_BLOCKED}
            target.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass
        return list(_BUILTIN_BLOCKED)
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        channels = data.get("blocked_channels", [])
        return channels if channels else list(_BUILTIN_BLOCKED)
    except Exception as exc:
        log.warning("Could not read blocked channels file %s: %s", target, exc)
        return list(_BUILTIN_BLOCKED)


def is_channel_blocked(
    source_id: Optional[str] = None,
    channel_id: Optional[str] = None,
    channel_url: Optional[str] = None,
    channel_name: Optional[str] = None,
    title: Optional[str] = None,
    file_path: Optional[Path] = None,
) -> bool:
    """Check if a creator, channel, or video matches any blocked criteria."""
    blocked_list = load_blocked_channels(file_path)
    if not blocked_list:
        return False

    def norm(s: Optional[str]) -> str:
        return (s or "").strip().lower()

    s_id = norm(source_id)
    c_id = norm(channel_id)
    c_url = norm(channel_url)
    c_name = norm(channel_name)
    v_title = norm(title)

    for entry in blocked_list:
        entry_id = norm(entry.get("id"))
        if s_id and entry_id and s_id == entry_id:
            return True

        for b_cid in entry.get("channel_ids", []):
            b_cid_norm = norm(b_cid)
            if b_cid_norm and (b_cid_norm == c_id or b_cid_norm in c_url):
                return True

        for b_handle in entry.get("handles", []):
            b_handle_norm = norm(b_handle).lstrip("@")
            if b_handle_norm and (b_handle_norm in c_url or b_handle_norm in c_name):
                return True

        for kw in entry.get("keywords", []):
            kw_norm = norm(kw)
            if not kw_norm:
                continue
            if kw_norm in c_name or kw_norm in s_id:
                return True
            if kw_norm in v_title:
                return True

    return False
