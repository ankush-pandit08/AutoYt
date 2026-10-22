import httpx
import pytest

from autoyt import limits
from autoyt.ai import LLM, Audit
from autoyt.groq_limits import TokenBudget, rate_error, seconds
from autoyt.topic_research import WebSourceReview


@pytest.mark.parametrize("value,expected", [("1m2.5s", 62.5), ("105ms", .105),
                                          ("2h3m", 7380), ("nan", None), ("-1", None)])
def test_duration_parser(value, expected):
    assert seconds(value) == expected


def test_daily_error_is_sanitized_and_distinct():
    response = httpx.Response(429, json={"error": {"message":
        "private-key organization-secret tokens per day (TPD). Please try again in 2h3m."}})
    assert rate_error(response) == ("tokens per day", 7380, False)


def test_token_pacing_waits_before_another_large_request(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr("autoyt.groq_limits.time.monotonic", lambda: clock[0])
    waits = []
    monkeypatch.setattr(limits, "pause", lambda delay: waits.append(delay))
    budget = TokenBudget()
    budget.observe(httpx.Response(200, headers={"x-ratelimit-limit-tokens": "8000",
        "x-ratelimit-remaining-tokens": "1000", "x-ratelimit-reset-tokens": "52.5s"}))
    budget.wait([{"content": "x"*3000}], 384)
    assert len(waits) == 1 and 3 < waits[0] < 4
    clock[0] += 60
    budget.wait([{"content": "x"*3000}], 384)
    assert len(waits) == 1


def test_short_term_limit_can_recover_during_research(cfg, monkeypatch):
    cfg.llm_provider, cfg.groq_api_key = "groq", "private-key"
    calls, waits = [], []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        if len(calls) == 1:
            return httpx.Response(429, headers={"retry-after": "2"},
                json={"error": {"message": "tokens per minute (TPM), private-key"}},
                request=httpx.Request("POST", url))
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"credible":true,"reason":"Documentation"}'}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", lambda delay: waits.append(delay))
    with limits.research_budget(240):
        assert LLM(cfg).structured(WebSourceReview, "Check source", {}).credible
    assert waits == [2.25]
    assert calls[0]["max_completion_tokens"] == 384
    assert calls[0]["reasoning_effort"] == "none"


def test_daily_limit_does_not_retry_or_expose_provider_message(cfg, monkeypatch):
    cfg.llm_provider, cfg.groq_api_key = "groq", "private-key"
    calls = []
    def post(url, **kwargs):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "3600"},
            json={"error": {"message": "organization-private-key tokens per day (TPD)"}}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", lambda _: pytest.fail("Daily quota must not sleep/retry"))
    with pytest.raises(RuntimeError, match="tokens per day.*3600 seconds") as caught:
        LLM(cfg).structured(Audit, "check", {})
    assert "private-key" not in str(caught.value) and len(calls) == 1


def test_wait_respects_the_shared_research_deadline(cfg, monkeypatch):
    cfg.llm_provider, cfg.groq_api_key = "groq", "private-key"
    clock = [100.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(limits.time, "sleep", lambda duration: clock.__setitem__(0, clock[0]+duration))
    def post(url, **kwargs):
        return httpx.Response(429, headers={"retry-after": "50"}, request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(RuntimeError, match="Research time limit"):
        with limits.research_budget(30):
            LLM(cfg).structured(Audit, "check", {})


def test_successful_calls_use_token_headers_to_pace_the_next_call(cfg, monkeypatch):
    cfg.llm_provider, cfg.groq_api_key = "groq", "private-key"
    waits, calls = [], []
    def post(url, **kwargs):
        calls.append(kwargs["json"])
        return httpx.Response(200, headers={"x-ratelimit-limit-tokens": "8000",
            "x-ratelimit-remaining-tokens": "0", "x-ratelimit-reset-tokens": "60s"},
            json={"choices": [{"message": {"content": '{"supported":true,"reasons":[]}'}}]},
            request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    monkeypatch.setattr(limits, "pause", lambda delay: waits.append(delay))
    llm = LLM(cfg)
    with limits.research_budget(240):
        for _ in range(2):
            assert llm.structured(Audit, "Check facts", {}).supported
    assert len(calls) == 2 and len(waits) == 1 and 3 < waits[0] < 10
