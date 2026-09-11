from __future__ import annotations

import asyncio
from collections.abc import Callable
from functools import lru_cache
from typing import Any

from app.services.stt.base import (
    ProviderError,
    ProviderNoSpeech,
    ProviderTranscription,
    ProviderUnavailable,
    ProviderUnexpectedResponse,
    STTProvider,
)

Pipeline = Callable[[bytes], dict[str, Any]]
PipelineLoader = Callable[[str, int], Pipeline]


def preload_phowhisper(model_id: str, device: int) -> None:
    """Download and load PhoWhisper so readiness implies inference is available."""
    _load_pipeline(model_id, device)


class PhoWhisperSTTProvider(STTProvider):
    """Local Vietnamese transcription through VinAI's PhoWhisper model.

    Transformers imports and model initialization are deferred until the first
    transcription. The default loader is cached by model and device, so a
    newly-created provider for each HTTP request reuses the loaded pipeline.
    """

    def __init__(
        self,
        *,
        model_id: str,
        device: int = -1,
        pipeline_loader: PipelineLoader | None = None,
    ) -> None:
        self._model_id = model_id
        self._device = device
        self._pipeline_loader = pipeline_loader or _load_pipeline

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        language: str,
        keyterms: list[str],
    ) -> ProviderTranscription:
        # PhoWhisper's standard pipeline has no keyterm prompting parameter.
        # Keep the public API provider-neutral and intentionally ignore it here.
        del filename, content_type, language, keyterms

        try:
            result = await asyncio.to_thread(self._transcribe_sync, audio_bytes)
        except ProviderError:
            raise
        except (ImportError, FileNotFoundError, OSError) as exc:
            raise ProviderUnavailable from exc
        except Exception as exc:
            raise ProviderUnavailable from exc

        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise ProviderUnexpectedResponse
        if not text.strip():
            raise ProviderNoSpeech

        return ProviderTranscription(text=text.strip(), language_code="vie")

    def _transcribe_sync(self, audio_bytes: bytes) -> dict[str, Any]:
        return self._pipeline_loader(self._model_id, self._device)(audio_bytes)


@lru_cache(maxsize=4)
def _load_pipeline(model_id: str, device: int) -> Pipeline:
    """Load and retain a Transformers ASR pipeline for the selected runtime."""
    from transformers import pipeline

    return pipeline(
        task="automatic-speech-recognition",
        model=model_id,
        device=device,
    )
