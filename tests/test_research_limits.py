from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from autoyt import ai, limits, pipeline, topic_research
from autoyt.models import Topic


def test_nested_research_shares_budget_and_cleans_up(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
    with limits.research_budget(30):
        clock[0] += 20
        with limits.research_budget(240):
            assert limits.timeout(60) == 10
        clock[0] += 11
        with pytest.raises(RuntimeError, match="Research time limit"):
            limits.check()
        clock[0] -= 11
    assert not limits.active()
    assert limits.timeout(60) == 60


@pytest.mark.parametrize("provider", ["gemini", "groq", "ollama"])
def test_research_transport_failure_does_not_retry_provider(cfg, monkeypatch, provider):
    cfg.llm_provider = provider
    cfg.gemini_api_key = cfg.groq_api_key = "test-only"
    calls = []
    def post(*args, **kwargs):
        calls.append(kwargs["timeout"])
        raise httpx.ReadTimeout("private provider details")
    monkeypatch.setattr(httpx, "post", post)
    with limits.research_budget(240), limits.request_limit(25):
        with pytest.raises(RuntimeError, match="timed out") as caught:
            ai.LLM(cfg).structured(ai.Audit, "Check source", {})
    assert "private" not in str(caught.value)
    assert len(calls) == 1
    timeout = calls[0].read if isinstance(calls[0], httpx.Timeout) else calls[0]
    assert timeout <= 25


def test_source_provider_failure_is_not_treated_as_bad_article(cfg, research_data, monkeypatch, tmp_path):
    source = research_data.sources[1].model_copy(update={"primary": False,
        "url": "https://expert.example/processor", "title": "processor computer"})
    monkeypatch.setattr(topic_research, "source_urls", lambda *args: (
        "processor computer", [source.url, "https://other.example/page"]))
    monkeypatch.setattr(topic_research.research, "extract", lambda *a, **kw: source)
    calls = []
    def review(*args):
        calls.append(1)
        raise RuntimeError("TRANSIENT: Gemini quota reached")
    with pytest.raises(RuntimeError, match="quota"):
        topic_research.gather(cfg, SimpleNamespace(article=lambda _: "text"),
                              SimpleNamespace(structured=review), SimpleNamespace(), tmp_path)
    assert calls == [1]
    assert (tmp_path / "source_retrievals.json").exists()
    assert not limits.active()


def test_pipeline_budget_covers_candidate_fallbacks(cfg, monkeypatch):
    cfg.research_timeout_seconds = 30
    clock = [100.0]
    monkeypatch.setattr(limits.time, "monotonic", lambda: clock[0])
    candidates = [Topic(title=title, url=f"https://example.com/{i}",
                        published_at=datetime.now(timezone.utc), source="test")
                  for i, title in enumerate(["Google AI processor update", "Microsoft cloud security update",
                                              "Apple developer tools update"])]
    monkeypatch.setattr(pipeline.discovery, "discover", lambda *args: candidates)
    calls = []
    def research(*args):
        limits.progress("Checking test evidence")
        calls.append(args[3].title)
        clock[0] += 20
        raise ValueError("No evidence")
    monkeypatch.setattr(pipeline.research, "research", research)
    monkeypatch.setattr(pipeline.youtube, "upload", lambda *args: pytest.fail("No upload allowed"))
    runner = pipeline.Pipeline(cfg)
    job_id = runner.create_run(dry_run=True)
    job = runner.store.get(job_id)
    assert job["state"] == "FAILED" and job["stage"] == 1
    assert "Research time limit" in job["error"]
    assert len(calls) == 2
    assert runner.store.load(job_id, "research_progress")["limit_seconds"] == 30
    assert not limits.active()
