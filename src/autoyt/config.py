from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    timezone: str = "Asia/Kolkata"
    schedule_times: str = "06:00,07:40,09:20,11:00,12:40,14:20,16:00,17:40,19:20,21:00"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_provider: Literal["ollama", "groq", "gemini"] = "ollama"
    gemini_api_key: str = Field(default="", repr=False)
    gemini_model: str = Field(default="gemini-3.8-flash", pattern=r"^[a-zA-Z0-9._-]+$")
    groq_api_key: str = Field(default="", repr=False)
    groq_model: str = "qwen/qwen3.8-27b"
    ollama_model: str = "qwen3:4b-instruct"
    ollama_embedding_model: str = "all-minilm:22m"
    ollama_context_length: int = Field(default=4096, ge=4096, le=16384)
    ollama_generation_timeout_seconds: int = Field(default=600, ge=60, le=1800)
    tts_engine: Literal["kokoro", "sapi", "piper"] = "kokoro"
    tts_voice: str = "af_heart"
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
    max_videos_per_day: int = Field(default=10, ge=1, le=10)
    topic_dedup_days: int = Field(default=14, ge=1)
    min_free_gb: float = Field(default=2, ge=0)
    retention_days: int = Field(default=14, ge=1)
    output_dir: Path = Path("output/jobs")
    cache_dir: Path = Path("data/cache")
    database_path: Path = Path("data/database/autoyt.db")
    google_trends_region: str = "IN"
    rss_feeds: list[str] = ["https://blog.google/rss/", "https://blogs.microsoft.com/feed/",
                            "https://www.theverge.com/rss/index.xml", "https://techcrunch.com/feed/",
                            "https://feeds.arstechnica.com/arstechnica/index",
                            "https://openai.com/news/rss.xml", "https://www.bleepingcomputer.com/feed/",
                            "https://www.securityweek.com/feed/", "https://feeds.feedburner.com/TheHackersNews"]
    primary_domains: list[str] = ["blog.google", "blogs.microsoft.com", "microsoft.com", "openai.com",
                                  "apple.com", "developer.android.com", "nvidia.com", "cisa.gov",
                                  "android-developers.googleblog.com", "blog.chromium.org",
                                  "connectwise.com", "drupal.org", "npmjs.com", "safedep.io",
                                  "github.com", "socket.dev", "huntress.com", "replit.com",
                                  "cloudflare.com", "about.fb.com"]
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
