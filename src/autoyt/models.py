from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Topic(BaseModel):
    title: str
    url: str
    published_at: datetime
    source: str
    volume: int | None = None
    related_urls: list[str] = []
    score: float = 0


class Source(BaseModel):
    url: str
    title: str
    published_at: datetime
    updated_at: datetime | None = None
    date_basis: str = "publication_metadata"
    retrieved_at: datetime
    text: str
    primary: bool
    publisher: str


class Evidence(BaseModel):
    source_url: str
    quote: str = Field(min_length=25, max_length=700)


class Claim(BaseModel):
    claim: str = Field(min_length=15, max_length=500)
    attribution: str | None = None
    evidence: list[Evidence] = Field(min_length=2)
    verification_status: Literal["verified"] = "verified"


class Research(BaseModel):
    sources: list[Source]
    claims: list[Claim] = Field(min_length=2, max_length=3)


class Scene(BaseModel):
    narration: str = Field(min_length=10, max_length=500)
    visual_query: str = Field(min_length=3, max_length=100)
    headline: str = Field(min_length=3, max_length=65)
    claim_ids: list[int] = Field(min_length=1)


class Script(BaseModel):
    topic: str
    title: str = Field(min_length=10, max_length=90)
    hook: str = Field(min_length=10, max_length=200)
    script: str
    description: str = Field(min_length=30, max_length=2500)
    hashtags: list[str] = Field(min_length=1, max_length=6)
    visual_keywords: list[str] = Field(min_length=1, max_length=10)
    thumbnail_text: str = Field(min_length=3, max_length=40)
    scenes: list[Scene] = Field(min_length=6, max_length=10)
    source_urls: list[str] = Field(min_length=2)
    needs_synthetic_media_disclosure: bool = False

    @model_validator(mode="after")
    def consistent(self):
        joined = " ".join(x.narration.strip() for x in self.scenes)
        if " ".join(self.script.split()) != " ".join(joined.split()):
            raise ValueError("script must exactly concatenate scene narration")
        if not self.script.startswith(self.hook):
            raise ValueError("hook must start narration")
        if not 60 <= len(self.script.split()) <= 115:
            raise ValueError("Narration needs 60–115 words")
        if any(x in self.model_dump_json().lower() for x in ("lorem ipsum", "placeholder", "insert here")):
            raise ValueError("Unresolved placeholder")
        if any(not x.startswith("#") or " " in x for x in self.hashtags):
            raise ValueError("Invalid hashtag")
        return self
