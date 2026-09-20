from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WordTiming(BaseModel):
    text: str
    start: float | None = None
    end: float | None = None
    speaker_id: str | None = None
    logprob: float | None = None


class TranscriptionResponse(BaseModel):
    request_id: str
    text: str
    raw_text: str
    refined: bool = False
    refinement_status: Literal["disabled", "refined", "unchanged", "fallback", "skipped"] = "disabled"
    language_code: str | None = None
    language_probability: float | None = None
    words: list[WordTiming] = Field(default_factory=list)
    provider: str
    model: str


class HealthResponse(BaseModel):
    status: str = "ok"
    provider: str
    configured: bool
