from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Topic(BaseModel):
    title: str
    url: str
    published_at: datetime | None
    source: str
    content_kind: Literal["news", "evergreen"] = "news"
    volume: int | None = None
    related_urls: list[str] = []
    creator_references: list[dict[str, str]] = []
    youtube_metadata: dict = Field(default_factory=dict)
    score: float = 0

    @model_validator(mode="after")
    def dated_news(self):
        if self.content_kind == "news" and self.published_at is None:
            raise ValueError("News topics require a publication date")
        return self


class Source(BaseModel):
    url: str
    title: str
    published_at: datetime | None
    updated_at: datetime | None = None
    date_basis: str = "publication_metadata"
    retrieved_at: datetime
    text: str
    primary: bool
    reviewed_web: bool = False
    publisher: str


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(min_length=25, max_length=700)


class Claim(BaseModel):
    claim: str = Field(min_length=15, max_length=500)
    attribution: str | None = None
    evidence: list[Evidence] = Field(min_length=1)
    verification_status: Literal["verified", "official_source"] = "verified"


class Research(BaseModel):
    sources: list[Source]
    claims: list[Claim] = Field(min_length=2, max_length=3)


class Scene(BaseModel):
    target_seconds: float | None = Field(default=None, gt=0, le=45)
    narration: str = Field(min_length=10, max_length=500)
    visual_query: str = Field(min_length=3, max_length=100)
    headline: str = Field(min_length=3, max_length=65)
    claim_ids: list[int] = Field(default_factory=list)


class Script(BaseModel):
    natural_pacing: bool = False
    topic: str
    title: str = Field(min_length=10, max_length=90)
    hook: str = Field(min_length=10, max_length=200)
    script: str
    description: str = Field(min_length=30, max_length=2500)
    hashtags: list[str] = Field(min_length=1, max_length=6)
    visual_keywords: list[str] = Field(min_length=1, max_length=10)
    thumbnail_text: str = Field(min_length=3, max_length=40)
    scenes: list[Scene] = Field(min_length=1, max_length=10)
    source_urls: list[str] = Field(default_factory=list)
    source_basis: Literal["verified", "official", "youtube", "user_input"] = "verified"
    needs_synthetic_media_disclosure: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.source_basis == "verified" and (len(self.source_urls) < 2 or any(
                not scene.claim_ids for scene in self.scenes)):
            raise ValueError("Verified scripts need two source URLs and evidence references in each scene")
        if self.source_basis == "official" and (not self.source_urls or any(not scene.claim_ids for scene in self.scenes)):
            raise ValueError("Official-source scripts need source URLs and evidence references")
        if self.source_basis == "youtube" and not self.source_urls:
            raise ValueError("YouTube scripts need a source video URL")
        if self.source_basis not in {"verified", "official"} and any(scene.claim_ids for scene in self.scenes):
            raise ValueError("Unchecked scripts cannot claim verified evidence references")
        joined = " ".join(x.narration.strip() for x in self.scenes)
        if " ".join(self.script.split()) != " ".join(joined.split()):
            raise ValueError("script must exactly concatenate scene narration")
        if not self.script.startswith(self.hook):
            raise ValueError("hook must start narration")
        if self.natural_pacing:
            if len(self.scenes) > 6:
                raise ValueError("Natural Shorts support one to six scenes")
            if not 12 <= len(self.script.split()) <= 180:
                raise ValueError("Natural Shorts narration needs 12–180 words")
        else:
            if len(self.scenes) < 6:
                raise ValueError("Standard scripts need at least six scenes")
            if not 60 <= len(self.script.split()) <= 115:
                raise ValueError("Narration needs 60–115 words")
        if any(x in self.model_dump_json().lower() for x in ("lorem ipsum", "placeholder", "insert here")):
            raise ValueError("Unresolved placeholder")
        if any(not x.startswith("#") or " " in x for x in self.hashtags):
            raise ValueError("Invalid hashtag")
        return self
