"""Bounded script-provider failover without changing saved preferences or quality rules."""
import httpx

PROVIDERS = ("groq", "gemini", "openrouter", "ollama")


def failure_reason(error):
    if not isinstance(error, RuntimeError):
        return None
    message = str(error).lower()
    if not message.startswith(("transient:", "configuration:")) or "research time limit" in message:
        return None
    if any(term in message for term in ("quota", "rate-limit", "rate limit", "limit reached",
                                        "allowance exhausted", "tokens per")):
        return "rate_limited"
    if any(term in message for term in ("key invalid", "access denied", "model missing", "insufficient ram",
                                        "request rejected", "free-model access")):
        return "access_unavailable"
    if any(term in message for term in ("unavailable", "timed out", "connection failed", "cannot connect",
                                        "request failed", "no available compatible endpoint")):
        return "unavailable"
    return None


def available(cfg, provider):
    if provider != "ollama":
        return bool(getattr(cfg, provider + "_api_key").strip())
    try:
        response = httpx.get(cfg.ollama_base_url + "/api/tags", timeout=2)
        response.raise_for_status()
        return cfg.ollama_model in {item["name"] for item in response.json()["models"]}
    except (httpx.HTTPError, KeyError, TypeError, ValueError):
        return False
