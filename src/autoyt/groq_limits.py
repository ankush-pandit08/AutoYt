"""Groq token-bucket pacing and sanitized rate-limit diagnostics."""
import math
import re
import time

from . import limits


def seconds(value):
    value = str(value).strip()
    try:
        result = float(value)
    except ValueError:
        parts = re.findall(r"(\d+(?:\.\d+)?)(ms|s|m|h|d)", value)
        if not parts or "".join(number + unit for number, unit in parts) != value:
            return None
        result = sum(float(number) * {"ms": .001, "s": 1, "m": 60, "h": 3600, "d": 86400}[unit]
                     for number, unit in parts)
    return result if math.isfinite(result) and result >= 0 else None


def rate_error(response):
    try:
        message = response.json().get("error", {}).get("message", "")
    except (ValueError, AttributeError, TypeError):
        message = ""
    message = message if isinstance(message, str) else ""
    kind = "quota/rate"
    for abbreviation, label in [("TPD", "tokens per day"), ("RPD", "requests per day"),
                                ("TPM", "tokens per minute"), ("RPM", "requests per minute")]:
        if label in message.lower() or re.search(r"\b" + abbreviation + r"\b", message):
            kind = label
            break
    delay = seconds(response.headers.get("retry-after", ""))
    if delay is None:
        match = re.search(r"try again in\s+(\d[\dhms.]*)", message, re.I)
        delay = seconds(match[1].rstrip(".")) if match else None
    too_large = "request too large" in message.lower()
    # Only controlled labels and numeric timing leave this function, never remote bodies or organization IDs.
    return kind, delay, too_large


class TokenBudget:
    def __init__(self):
        self.limit = self.remaining = self.refill = self.observed = None

    def observe(self, response):
        try:
            limit = float(response.headers["x-ratelimit-limit-tokens"])
            remaining = float(response.headers["x-ratelimit-remaining-tokens"])
            reset = seconds(response.headers["x-ratelimit-reset-tokens"])
        except (KeyError, ValueError, TypeError):
            return
        if not all(math.isfinite(v) for v in (limit, remaining)) or not 0 <= remaining <= limit or not reset:
            return
        self.limit, self.remaining = limit, remaining
        self.refill = (limit - remaining) / reset
        self.observed = time.monotonic()

    def wait(self, messages, output_tokens):
        if not self.refill:
            return
        # Conservative estimate for mixed English/Hindi prompts, plus the requested output allowance.
        needed = min(self.limit, sum(len(m["content"].encode("utf-8")) for m in messages) / 3 + output_tokens + 64)
        available = min(self.limit, self.remaining + (time.monotonic() - self.observed) * self.refill)
        delay = max(0, (needed - available) / self.refill)
        if delay:
            limits.progress(f"Waiting {math.ceil(delay)}s for Groq token allowance")
            limits.pause(delay + .25)
