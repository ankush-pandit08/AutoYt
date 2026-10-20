# Preferred creator sources

Verified with YouTube channels.list(forHandle=...) on 20 October 2026:

- Varun Mayya: https://www.youtube.com/@VarunMayya — UCsQoiOrh7jzKmE8NBofhTnQ
- 100x Engineers: https://www.youtube.com/@100xEngineers — UCoch_d78Aosmp14ey1HgGSQ
- Aevy TV: https://www.youtube.com/@aevytv — UCA295QVkf9O1RQ8_-s3FVXg

Local personal environment and example settings enable these creator_channel_ids, prefer_tech_insights=true and topic_focus=ai_coding. The current YouTube discovery mode now reads their public upload playlists, fetches video metadata/statistics in bounded batches, and uses those creators as the topic pool. With creators configured, generic trending/search topics are not substituted. Relevant recent AI capabilities, launches, gadgets, wearables, robotics and future technology developments are preferred; generic tutorials, evergreen computer tips, courses and revenue stories are excluded. If no eligible topic exists, the run skips rather than filling the slot with unrelated content. Subscription state is not changed. Sources older than seven days, unrelated topics and the application's own channel remain excluded. Missing individual creator playlists do not block the others. Discovery cache identity includes the creator list and topic focus.

Scripts remain original, with source links retained in metadata and creator names excluded from narration and on-screen text; no transcripts or full creator videos are downloaded. Today's saved plan is not rewritten. Production deployments must apply the same public source settings while preserving the current saved plan, voice settings and upload schedule.

## Shorts script template

Each newly generated script follows six unequal beats: Hook (0–3s), Curiosity (3–8s), Big Reveal (8–16s), Explanation (16–30s), Real Impact (30–40s), Ending (40–45s). Beat word budgets favor a short hook and longer explanation. Requested beat durations are stored with generated scenes. Pitch-preserving FFmpeg tempo adjustment fits each beat, allowing a bounded closing pause. Extreme timing mismatches fail with an actionable message instead of cutting speech or publishing a broken Short. Subtitles and visuals follow the fitted audio. Legacy/manual scripts without beat targets retain their existing measured timing. Existing quality checks remain active.

Narration uses the selected English, Hindi or Hinglish setting. Hindi/Hinglish requires Devanagari in every scene. Creator names are checked before accepting generated scripts. Rumors, concepts, prototypes and company claims must remain qualified; a metadata-consistency audit rejects unsupported product details. Metadata is not independent fact verification, and thin descriptions may cause a script to be rejected instead of inventing a product mechanism. Manually supplied scripts keep their words and are not forced into generated beat word budgets. Existing saved plans are not silently rewritten.
