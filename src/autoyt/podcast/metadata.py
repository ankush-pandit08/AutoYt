"""Metadata generation for podcast Shorts clips.

Generates:
- Engaging title with #Shorts.
- Detailed description with creator attribution, original episode link,
  rights verification statement, and relevant topic hashtags.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List

from .sources import PodcastSource
from .utils import clean_to_english_hinglish

log = logging.getLogger(__name__)


@dataclass
class ClipMetadata:
    title: str
    description: str
    hashtags: List[str]
    tags: List[str]  # YouTube keyword tags


class MetadataGenerator:
    """Generates titles, descriptions, and tags with creator attribution and rights disclosure."""

    def generate(
        self,
        clip_title: str,
        clip_summary: str,
        source: PodcastSource,
        episode_title: str,
        episode_url: str,
        hashtags: List[str],
        rights_basis: str | None = None,
        include_summary: bool = True,
        include_rights_in_desc: bool = False,
        music_credit: str | None = None,
    ) -> ClipMetadata:
        # 1. Clean Title: Strictly English
        clean_title = clean_to_english_hinglish(clip_title.strip(), keep_emojis=True)
        if not clean_title.endswith("#Shorts"):
            clean_title = f"{clean_title} #Shorts"
        if len(clean_title) > 100:
            clean_title = clean_title[:92].rsplit(" ", 1)[0] + "... #Shorts"

        # 2. Creator tag formatting
        clean_source_name = clean_to_english_hinglish(source.name, keep_emojis=False) or source.name
        creator_tag = "#" + "".join(w.capitalize() for w in clean_source_name.split())
        clean_hashtags = [clean_to_english_hinglish(h, keep_emojis=False) for h in hashtags if h]
        all_tags = list(dict.fromkeys(clean_hashtags + [creator_tag, "#Shorts", "#Podcast", "#Motivation"]))

        # 3. Clean engaging description in English: hook summary + question CTA + creator attribution + hashtags
        desc_parts = []
        clean_summary = clean_to_english_hinglish(clip_summary.strip(), keep_emojis=True)
        if include_summary and clean_summary:
            desc_parts.append(clean_summary)
            desc_parts.append("")
            desc_parts.append("💬 What do you think about this? Drop your thoughts in the comments below! 👇")
            desc_parts.append("")

        clean_ep_title = clean_to_english_hinglish(episode_title, keep_emojis=True) or episode_title
        clean_show_name = clean_to_english_hinglish(source.show_name, keep_emojis=True) or source.show_name
        desc_parts.extend([
            "─────────────────────────────",
            f"🎙️ Podcast: {clean_show_name}",
            f"👤 Creator / Guest: {clean_source_name}",
            f"🎬 Original Episode: {clean_ep_title}",
            f"🔗 Watch the complete episode: {episode_url}",
            "─────────────────────────────",
        ])

        if include_rights_in_desc:
            rights_note = (
                f"Authorized via: {rights_basis}."
                if rights_basis
                else "Published with applicable creator authorization and attribution."
            )
            desc_parts.append("")
            desc_parts.append(f"ℹ️ Content Rights: {rights_note}")

        if music_credit and music_credit.strip():
            desc_parts.append(f"🎵 Background Music: {music_credit.strip()}")

        desc_parts.append("")
        desc_parts.append(" ".join(all_tags[:6]))

        full_description = "\n".join(desc_parts)

        if include_rights_in_desc:
            rights_note = (
                f"Authorized via: {rights_basis}."
                if rights_basis
                else "Published with applicable creator authorization and attribution."
            )
            desc_parts.append("")
            desc_parts.append(f"ℹ️ Content Rights: {rights_note}")

        if music_credit and music_credit.strip():
            desc_parts.append(f"🎵 Background Music: {music_credit.strip()}")

        desc_parts.append("")
        desc_parts.append(" ".join(all_tags[:6]))

        full_description = "\n".join(desc_parts)

        # 4. YouTube keyword tags
        yt_tags = [
            source.name,
            source.show_name,
            "shorts",
            "podcast clips",
            "motivation",
            "entrepreneurship",
            "personal growth",
        ]
        for t in all_tags:
            bare = t.lstrip("#")
            if bare.lower() not in [x.lower() for x in yt_tags]:
                yt_tags.append(bare)

        return ClipMetadata(
            title=clean_title,
            description=full_description,
            hashtags=all_tags,
            tags=yt_tags[:15],
        )
