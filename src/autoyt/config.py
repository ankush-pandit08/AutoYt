import os
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    timezone: str = "Asia/Kolkata"
    schedule_times: str = "09:30,11:18,13:06,14:54,16:42,18:30"
    daily_script_batch: bool = False
    morning_plan_time: str = Field(default="05:00", pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    content_mode: Literal["youtube_insights", "verified_news"] = "youtube_insights"
    youtube_discovery_region: str = Field(default="IN", pattern=r"^[A-Z]{2}$")
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_provider: Literal["ollama", "groq", "gemini", "openrouter"] = "ollama"
    openrouter_api_key: str = Field(default="", repr=False)
    openrouter_model: str = Field(default="google/gemma-4-31b-it:free", pattern=r"^[a-zA-Z0-9._/-]+:free$")
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = Field(default="gemini-3.8-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    gemini_thinking_level: Literal["minimal", "low", "medium", "high"] = "low"
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "qwen/qwen3.8-27b"
    ollama_model: str = "gemma3:4b"
    ollama_embedding_model: str = "all-minilm:22m"
    ollama_context_length: int = Field(default=8192, ge=4096, le=16384)
    ollama_generation_timeout_seconds: int = Field(default=600, ge=60, le=1800)
    ollama_research_timeout_seconds: int = Field(default=1800, ge=30, le=3600)
    tts_engine: Literal["kokoro", "sapi", "piper", "gemini", "indicf5", "elevenlabs"] = "gemini"
    gemini_tts_model: str = Field(default="gemini-3.8-flash-tts", pattern=r"^[a-zA-Z0-9._-]+$")
    indicf5_python: Path = Path(".venv-indicf5/Scripts/python.exe" if os.name == "nt" else ".venv-indicf5/bin/python")
    indicf5_model_dir: Path = Path("data/models/indicf5")
    indicf5_reference_audio: Path = Path("data/voices/indicf5-reference.wav")
    indicf5_reference_text_file: Path = Path("data/voices/indicf5-reference.txt")
    indicf5_steps: int = Field(default=16, ge=8, le=64)
    indicf5_threads: int = Field(default=2, ge=1, le=12)
    indicf5_timeout_seconds: int = Field(default=3600, ge=60, le=7200)
    elevenlabs_model_id: Literal["eleven_flash_v2_5"] = "eleven_flash_v2_5"
    elevenlabs_api_key: str = Field(default="", repr=False)
    tts_voice: str = Field(default_factory=lambda data: "Puck" if data.get("tts_engine") == "gemini"
                           else "" if data.get("tts_engine") == "elevenlabs" else "am_michael")
    tts_speed: float = Field(default=1.0, ge=0.85, le=1.15)
    narration_language: Literal["en", "hi"] = "en"
    script_style: Literal["standard", "hinglish"] = "standard"
    kokoro_model: Path = Path("data/models/kokoro/kokoro-v1.0.int8.onnx")
    kokoro_voices: Path = Path("data/models/kokoro/voices-v1.0.bin")
    piper_model: Path = Path("assets/voices/en_US.onnx")
    pexels_api_key: str = Field(default="", repr=False)
    pixabay_api_key: str = Field(default="", repr=False)
    youtube_channel_id: str = ""
    youtube_client_secrets_file: Path = Path("secrets/client_secret.json")
    youtube_token_file: Path = Path("secrets/youtube_token.json")
    youtube_privacy: Literal["private", "public"] = "private"
    youtube_made_for_kids: bool = False
    enable_upload: bool = False
    ffmpeg: str = "ffmpeg"
    ffprobe: str = "ffprobe"
    video_width: int = Field(default=1080, ge=180)
    video_height: int = Field(default=1920, ge=320)
    video_fps: int = Field(default=30, ge=24, le=60)
    video_duration_seconds: float = 45
    duration_min: float = 1
    duration_max: float = 60
    encoder_preset: str = "fast"
    crf: int = Field(default=20, ge=15, le=28)
    render_mode: Literal["normal", "motion_graphics", "hybrid"] = "normal"
    render_fallback: bool = True
    remotion_node: str = "node"
    remotion_concurrency: int = Field(default=1, ge=1, le=4)
    remotion_timeout_seconds: int = Field(default=1800, ge=30, le=7200)
    max_videos_per_day: int = Field(default=6, ge=1, le=10)
    topic_dedup_days: int = Field(default=14, ge=1)
    prefer_tech_insights: bool = False
    google_news_enabled: bool = True
    topic_focus: Literal["technology", "ai_coding"] = "technology"
    research_candidate_limit: int = Field(default=40, ge=12, le=100)
    research_timeout_seconds: int = Field(default=240, ge=30, le=900)
    llm_request_timeout_seconds: int = Field(default=60, ge=5, le=180)
    source_review_timeout_seconds: int = Field(default=25, ge=5, le=60)
    enable_evergreen_fallback: bool = False
    editorial_references: list[str] = []
    creator_channel_ids: list[str] = []
    enable_public_images: bool = False
    min_free_gb: float = Field(default=2, ge=0)
    retention_days: int = Field(default=14, ge=1)
    output_dir: Path = Path("output/jobs")
    cache_dir: Path = Path("data/cache")
    database_path: Path = Path("data/database/autoyt.db")
    google_trends_region: str = "IN"
    rss_feeds: list[str] = ["https://blog.google/rss/", "https://blogs.microsoft.com/feed/",
                            "https://deepmind.google/blog/rss.xml", "https://huggingface.co/blog/feed.xml",
                            "https://github.blog/ai-and-ml/feed/", "https://devblogs.microsoft.com/ai/feed/",
                            "https://techcrunch.com/category/artificial-intelligence/feed/",
                            "https://www.theverge.com/rss/index.xml", "https://techcrunch.com/feed/",
                            "https://feeds.arstechnica.com/arstechnica/index",
                            "https://openai.com/news/rss.xml", "https://www.bleepingcomputer.com/feed/",
                            "https://www.securityweek.com/feed/", "https://feeds.feedburner.com/TheHackersNews",
                            "https://www.engadget.com/rss.xml", "https://www.androidauthority.com/feed/",
                            "https://9to5google.com/feed/"]
    primary_domains: list[str] = ["blog.google", "blogs.microsoft.com", "microsoft.com", "openai.com",
                                  "apple.com", "developer.android.com", "nvidia.com", "cisa.gov",
                                  "android-developers.googleblog.com", "blog.chromium.org",
                                  "connectwise.com", "drupal.org", "npmjs.com", "safedep.io",
                                  "github.com", "socket.dev", "huntress.com", "replit.com",
                                  "cloudflare.com", "about.fb.com", "anthropic.com", "deepmind.google",
                                  "huggingface.co", "github.blog", "mistral.ai", "cursor.com",
                                  "ai.meta.com", "ollama.com", "ibm.com", "aws.amazon.com",
                                  "developers.google.com", "fidoalliance.org", "webauthn.guide",
                                  "git-scm.com", "atlassian.com", "tesla.com", "spacex.com", "meta.com",
                                  "about.meta.com", "netflix.com", "research.netflix.com", "elevenlabs.io"]
    local_media_manifest: Path = Path("assets/footage/manifest.json")

    @model_validator(mode="after")
    def check(self):
        ZoneInfo(self.timezone)
        slots = self.schedule_times.split(",")
        if not 1 <= len(slots) <= 10 or len(set(slots)) != len(slots):
            raise ValueError("Configure one to ten distinct daily slots")
        for slot in slots:
            if len(slot) != 5 or slot[2] != ":" or not slot.replace(":", "").isdigit():
                raise ValueError("Schedule times must use HH:MM")
            h, m = map(int, slot.split(":"))
            if not (0 <= h <= 23 and 0 <= m <= 59):
                raise ValueError("Invalid schedule time")
        if self.daily_script_batch and self.morning_plan_time >= min(slots):
            raise ValueError("Morning script preparation must start before the first video slot")
        if not (0 < self.duration_min <= self.video_duration_seconds <= self.duration_max):
            raise ValueError("Invalid duration tolerance")
        if self.video_width * 16 != self.video_height * 9 or self.video_width % 2:
            raise ValueError("Video dimensions must be even 9:16")
        if self.enable_upload and not self.youtube_channel_id:
            raise ValueError("Set YOUTUBE_CHANNEL_ID before enabling uploads")
        if not self.ollama_base_url.startswith(("http://127.0.0.1:", "http://localhost:")):
            raise ValueError("Ollama must run locally")
        return self

    def prepare(self):
        for path in (self.output_dir, self.cache_dir, self.database_path.parent, Path("data/logs")):
            path.mkdir(parents=True, exist_ok=True)
