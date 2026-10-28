"""Bounded provider backoff, independent of structured-output repair attempts."""
import math
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

from . import limits
from .groq_limits import seconds


def retry_after(response):
    value = response.headers.get("retry-after", "")
    if not value:
        try:
            for detail in response.json().get("error", {}).get("details", []):
                if isinstance(detail, dict) and detail.get("retryDelay"):
                    value = detail["retryDelay"]
                    break
        except (ValueError, AttributeError, TypeError):
            pass
    if not value:
        try:
            headers = response.json().get("error", {}).get("metadata", {}).get("headers", {})
            headers = {str(k).lower(): v for k, v in headers.items()}
            value = headers.get("retry-after", "")
            if not value and headers.get("x-ratelimit-reset"):
                reset = float(headers["x-ratelimit-reset"])
                if not math.isfinite(reset):
                    return None
                if reset > 100000000000:
                    reset /= 1000
                return max(0, reset - datetime.now(timezone.utc).timestamp())
        except (ValueError, AttributeError, TypeError, OverflowError):
            pass
    delay = seconds(value)
    if delay is not None:
        return delay
    try:
        return max(0, (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds())
    except (ValueError, TypeError, OverflowError):
        return None


def request(send, provider, rate_info, *, fail_fast=False):
    # The LLM coordinator switches providers; direct calls retain bounded backoff.
    for attempt in range(4):
        limits.check()
        response = send()
        status = response.status_code
        if response.is_success and provider == "OpenRouter":
            try:
                code = response.json().get("error", {}).get("code")
                if str(code).isdigit():
                    status = int(code)
            except (ValueError, AttributeError, TypeError):
                pass
        if status == 429:
            kind, delay, permanent = rate_info(response)
            if permanent or fail_fast:
                return response
        elif status in (500, 502, 503, 504):
            if fail_fast:
                return response
            kind, delay = "service availability", retry_after(response)
        else:
            return response
        if attempt == 3:
            return response
        # Allow a useful refill, rather than repeatedly retrying at a token boundary.
        delay = max(delay + .25 if delay is not None else 0, 3 * 2 ** attempt)
        if delay > 60 or limits.timeout(delay + 1) < delay + 1:
            return response
        limits.progress(f"Waiting {math.ceil(delay)}s for {provider} {kind}; retry {attempt + 1}/3")
        limits.pause(delay)
    return response
