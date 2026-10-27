"""Revise words from measured speech; never change playback speed or add silence."""
import math

MAX_REVISIONS = 3


def feedback(cfg, script, seconds):
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("Narration has no measurable positive duration")
    count = len(script.script.split())
    target = (cfg.duration_min + cfg.duration_max) / 2
    target_words = max(12, min(180, round(count / seconds * target)))
    low = max(12, math.ceil(target_words * .9))
    high = min(180, math.floor(target_words * 1.1))
    direction = "EXPAND" if seconds < cfg.duration_min else "SHORTEN"
    # A correction must move in the required direction, even near the bounds.
    if direction == "EXPAND":
        low = max(low, count + 1)
    else:
        high = min(high, count - 1)
    if low > high:
        raise ValueError("Narration cannot fit the duration limits within the supported 12–180 word range")
    return {"kind": "measured_duration", "direction": direction,
            "measured_seconds": seconds, "measured_words": count,
            "minimum_seconds": cfg.duration_min, "maximum_seconds": cfg.duration_max,
            "target_words": target_words, "minimum_words": low, "maximum_words": high,
            "previous_draft": {"title": script.title, "description": script.description,
                "thumbnail_text": script.thumbnail_text,
                "narration": [s.narration for s in script.scenes],
                "headlines": [s.headline for s in script.scenes],
                "visual_queries": [s.visual_query for s in script.scenes]}}


def instruction(value):
    if not isinstance(value, dict) or value.get("kind") != "measured_duration":
        return ""
    return (f"{value['direction']} the previous narration: its {value['measured_words']} words measured "
            f"{value['measured_seconds']:.2f} seconds at the selected voice's NORMAL speed. "
            f"Use {value['minimum_words']}–{value['maximum_words']} TOTAL spoken words "
            f"(aim for {value['target_words']}) to fit {value['minimum_seconds']:g}–"
            f"{value['maximum_seconds']:g} seconds. This measured word budget overrides generic "
            "word-count and concise/shorten instructions. Edit the supplied previous_draft, keeping its "
            "topic, supported facts and attribution. Expand only by explaining existing evidence clearly; "
            "shorten by removing repetition. No invented facts, filler, repeated sentences, silence, "
            "or speaking-speed changes. Keep complete sentences and natural delivery. ")


def validate(script, value):
    if not instruction(value):
        return
    count = len(script.script.split())
    if not value['minimum_words'] <= count <= value['maximum_words']:
        direction = "EXPAND" if count < value['minimum_words'] else "SHORTEN"
        raise ValueError(f"{direction}: revised narration has {count} words; measured voice requires "
                         f"{value['minimum_words']}–{value['maximum_words']} words. Preserve the evidence.")
