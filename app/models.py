from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class WordTiming(BaseModel):
    text: str
    start: float | None = None
    end: float | None = None
    speaker_id: str | None = None
    logprob: float | None = None


class IntentSlot(BaseModel):
    name: str
    value: str | int | float | bool | None = None
    entity_id: str | None = None
    source_text: str | None = None
    confidence: float = Field(default=1.0, ge=0, le=1)


class IntentInterpretation(BaseModel):
    intent: str
    status: Literal["complete", "incomplete", "ambiguous"]
    confidence: float = Field(ge=0, le=1)
    actionable: bool = False
    negated: bool = False
    slots: list[IntentSlot] = Field(default_factory=list)
    missing_slots: list[str] = Field(default_factory=list)
    ambiguous_slots: list[str] = Field(default_factory=list)
    clarification: str | None = None


class TranscriptionResponse(BaseModel):
    request_id: str
    text: str
    raw_text: str
    refined: bool = False
    refinement_status: Literal["disabled", "refined", "unchanged", "fallback", "skipped"] = "disabled"
    interpretation: IntentInterpretation | None = None
    language_code: str | None = None
    language_probability: float | None = None
    words: list[WordTiming] = Field(default_factory=list)
    provider: str
    model: str


class HealthResponse(BaseModel):
    status: str = "ok"
    provider: str
    configured: bool
