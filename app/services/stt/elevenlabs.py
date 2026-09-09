from __future__ import annotations

from collections.abc import Callable

import httpx

from app.services.stt.base import (
    ProviderNoSpeech,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTranscription,
    ProviderUnavailable,
    ProviderUnexpectedResponse,
    ProviderWord,
    STTProvider,
)


class ElevenLabsSTTProvider(STTProvider):
    """Small adapter around ElevenLabs' batch Speech-to-Text endpoint."""

    endpoint = "https://api.elevenlabs.io/v1/speech-to-text"

    def __init__(
        self,
        *,
        api_key: str,
        model_id: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._model_id = model_id
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def transcribe(
        self,
        *,
        audio_bytes: bytes,
        filename: str,
        content_type: str,
        language: str,
        keyterms: list[str],
    ) -> ProviderTranscription:
        files: list[tuple[str, tuple[None | str, str | bytes, str | None]]] = [
            ("file", (filename, audio_bytes, content_type)),
            ("model_id", (None, self._model_id, None)),
            ("language_code", (None, language, None)),
        ]
        files.extend(("keyterms", (None, term, None)) for term in keyterms)

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self.endpoint,
                    headers={"xi-api-key": self._api_key},
                    files=files,
                )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailable from exc

        if response.status_code == 422:
            raise ProviderNoSpeech
        if response.status_code == 429:
            raise ProviderRateLimited
        if response.status_code in {401, 403}:
            raise ProviderUnavailable
        if response.status_code >= 500:
            raise ProviderUnavailable
        if response.is_error:
            raise ProviderUnexpectedResponse

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnexpectedResponse from exc

        text = payload.get("text")
        if not isinstance(text, str):
            raise ProviderUnexpectedResponse

        words: list[ProviderWord] = []
        for word in payload.get("words", []):
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                continue
            words.append(
                ProviderWord(
                    text=word["text"],
                    start=_as_float(word.get("start")),
                    end=_as_float(word.get("end")),
                    speaker_id=word.get("speaker_id") if isinstance(word.get("speaker_id"), str) else None,
                    logprob=_as_float(word.get("logprob")),
                )
            )

        return ProviderTranscription(
            text=text,
            language_code=payload.get("language_code")
            if isinstance(payload.get("language_code"), str)
            else None,
            language_probability=_as_float(payload.get("language_probability")),
            words=words,
        )


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
