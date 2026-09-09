from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from app.config import Settings, get_settings
from app.models import TranscriptionResponse, WordTiming
from app.services.stt.base import (
    ProviderError,
    ProviderNoSpeech,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
)
from app.services.stt.elevenlabs import ElevenLabsSTTProvider

router = APIRouter(prefix="/api/v1", tags=["transcriptions"])

SUPPORTED_MIME_TYPES = {
    "audio/mpeg",
    "audio/mp4",
    "audio/ogg",
    "audio/webm",
    "audio/wav",
    "audio/x-m4a",
    "audio/x-wav",
}
SUPPORTED_EXTENSIONS = {".m4a", ".mp3", ".mp4", ".ogg", ".wav", ".webm"}
MAX_KEYTERMS = 1_000
MAX_KEYTERM_LENGTH = 50


@router.post("/transcriptions", response_model=TranscriptionResponse)
async def create_transcription(
    request: Request,
    audio: UploadFile | None = File(None),
    language: str = Form("vie"),
    keyterms: str | None = Form(None),
):
    request_id = getattr(request.state, "request_id", str(uuid4()))

    if audio is None:
        return _error(400, "missing_audio", "Upload an audio file to transcribe.", request_id)

    settings = get_settings()

    if not settings.configured:
        return _error(
            503,
            "provider_not_configured",
            "Speech transcription is not configured. Add the server API key and try again.",
            request_id,
        )

    filename = audio.filename or "recording"
    content_type = (audio.content_type or "").lower()
    if not _is_supported_audio(filename, content_type):
        return _error(
            415,
            "unsupported_audio_type",
            "Upload WAV, MP3, M4A, WebM, OGG, or MP4 audio.",
            request_id,
        )

    audio_bytes = await audio.read(settings.max_upload_bytes + 1)
    if not audio_bytes:
        return _error(400, "empty_audio", "Upload a non-empty audio file.", request_id)
    if len(audio_bytes) > settings.max_upload_bytes:
        return _error(
            413,
            "audio_too_large",
            f"Audio must be no larger than {settings.max_upload_mb} MB.",
            request_id,
        )

    normalized_language = language.strip().lower() or "vie"
    if normalized_language not in {"vi", "vie", "vi-vn"}:
        return _error(400, "unsupported_language", "This POC supports Vietnamese only.", request_id)

    resolved_keyterms = _resolve_keyterms(settings, keyterms)
    if resolved_keyterms is None:
        return _error(
            400,
            "invalid_keyterms",
            "Use at most 1,000 keyterms, each no longer than 50 characters.",
            request_id,
        )

    provider = _build_provider(settings)
    try:
        result = await provider.transcribe(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type or "application/octet-stream",
            language="vie",
            keyterms=resolved_keyterms,
        )
    except ProviderNoSpeech:
        return _error(422, "no_speech_detected", "No recognizable speech was found in this audio.", request_id)
    except ProviderRateLimited:
        return _error(429, "provider_rate_limited", "The transcription provider is busy. Try again shortly.", request_id)
    except ProviderTimeout:
        return _error(504, "provider_timeout", "Transcription took too long. Try a shorter recording.", request_id)
    except (ProviderUnavailable, ProviderError):
        return _error(502, "provider_error", "The transcription provider could not process this audio.", request_id)

    return TranscriptionResponse(
        request_id=request_id,
        text=result.text,
        language_code=result.language_code,
        language_probability=result.language_probability,
        words=[
            WordTiming(
                text=word.text,
                start=word.start,
                end=word.end,
                speaker_id=word.speaker_id,
                logprob=word.logprob,
            )
            for word in result.words
        ],
        provider=settings.stt_provider,
        model=settings.stt_model_id,
    )


def _build_provider(settings: Settings) -> ElevenLabsSTTProvider:
    assert settings.elevenlabs_api_key is not None
    return ElevenLabsSTTProvider(
        api_key=settings.elevenlabs_api_key.get_secret_value(),
        model_id=settings.stt_model_id,
        timeout_seconds=settings.request_timeout_seconds,
    )


def _is_supported_audio(filename: str, content_type: str) -> bool:
    return content_type in SUPPORTED_MIME_TYPES or Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


def _resolve_keyterms(settings: Settings, requested: str | None) -> list[str] | None:
    values = settings.configured_keyterms + (requested or "").split(",")
    seen: set[str] = set()
    keyterms: list[str] = []
    for value in values:
        keyterm = value.strip()
        if not keyterm:
            continue
        if len(keyterm) > MAX_KEYTERM_LENGTH:
            return None
        normalized = keyterm.casefold()
        if normalized not in seen:
            seen.add(normalized)
            keyterms.append(keyterm)
    return keyterms if len(keyterms) <= MAX_KEYTERMS else None


def _error(status_code: int, code: str, message: str, request_id: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"request_id": request_id, "error": {"code": code, "message": message}},
    )
