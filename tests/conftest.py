from datetime import datetime, timezone

import pytest

from autoyt.config import Settings
from autoyt.models import Claim, Evidence, Research, Script, Source


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return Settings(_env_file=None, script_fallback_enabled=False, google_news_enabled=False, tts_engine="kokoro", tts_voice="am_michael", content_mode="verified_news", output_dir=tmp_path/"output", cache_dir=tmp_path/"cache",
                    database_path=tmp_path/"db.sqlite", min_free_gb=0)


def sample_script():
    texts = [
        "Your computer turns instructions into actions, and the processor is where much of that work gets done.",
        "Think of software as a set of directions, with each instruction contributing to a larger useful task.",
        "Memory holds information that running programs need, giving the processor a place to access their working data.",
        "Storage keeps files available between sessions, while memory supports the work happening as you use your computer.",
        "That distinction is useful when thinking about an upgrade, because different components serve different purposes in practice.",
        "Start with your actual workload, understand the bottleneck, and choose the change that helps your daily tasks."
    ]
    return Script(topic="Local render test: understanding your computer", title="How Your Computer Gets Work Done",
                  hook=texts[0], script=" ".join(texts), description="Local rendering demonstration, not a news report.",
                  hashtags=["#Shorts", "#Technology"], visual_keywords=["computer processor"],
                  thumbnail_text="LOCAL RENDER TEST",
                  scenes=[{"narration": t, "headline": h, "visual_query": "computer processor",
                           "claim_ids": [0]} for t, h in zip(texts, ["Instructions become actions", "Software gives directions",
                           "Memory holds working data", "Storage keeps your files", "Understand each component",
                           "Choose for your workload"])],
                  source_urls=["https://a.example/doc", "https://b.example/doc", "https://c.example/doc"])


@pytest.fixture
def script():
    return sample_script()


@pytest.fixture
def research_data():
    timestamp = datetime.now(timezone.utc)
    claim = "Processors execute instructions to perform useful computing tasks."
    quotes = [claim + " Software provides these directions for the computer.",
              claim + " This vendor announcement focuses on processor design.",
              "Independent reporting covers memory, storage, and the computer industry in detail."]
    sources = [Source(url=f"https://{name}.example/doc", title=name, text=quote,
                      published_at=timestamp, retrieved_at=timestamp, primary=i == 0, publisher=name)
               for i, (name, quote) in enumerate(zip("abc", quotes))]
    verified = Claim(claim=claim, evidence=[Evidence(source_url=s.url, quote=s.text) for s in sources[:2]])
    return Research(sources=sources, claims=[verified, verified.model_copy()])
