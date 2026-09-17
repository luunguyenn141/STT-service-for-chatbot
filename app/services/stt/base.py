from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(slots=True)
class ProviderWord:
    text: str
    start: float | None = None
    end: float | None = None
    speaker_id: str | None = None
    logprob: float | None = None


@dataclass(slots=True)
class ProviderTranscription:
    text: str
    language_code: str | None = None
    language_probability: float | None = None
    words: list[ProviderWord] = field(default_factory=list)


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        language: str,
        keyterms: list[str],
    ) -> ProviderTranscription:
        """Convert an audio payload into a provider-neutral transcription."""


class ProviderError(Exception):
    """Base class for safe, provider-independent failures."""


class ProviderNoSpeech(ProviderError):
    pass


class ProviderInvalidAudio(ProviderError):
    pass


class ProviderRateLimited(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class ProviderTimeout(ProviderError):
    pass


class ProviderUnexpectedResponse(ProviderError):
    pass
