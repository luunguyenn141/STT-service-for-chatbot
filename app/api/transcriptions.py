from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
import secrets
import time
from uuid import uuid4

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import Settings, get_settings
from app.models import TranscriptionResponse, WordTiming
from app.services.rate_limiter import rate_limiter
from app.services.stt.base import (
    ProviderError,
    ProviderNoSpeech,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderUnavailable,
    STTProvider,
)
from app.services.stt.elevenlabs import ElevenLabsSTTProvider
from app.services.stt.phowhisper import PhoWhisperSTTProvider
from app.services.text_refiner import refine_text

logger = logging.getLogger("stt_poc.audit")
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


@dataclass(slots=True)
class AudioSubmission:
    audio_bytes: bytes
    filename: str
    content_type: str
    language: str
    keyterms: str | None


@router.post(
    "/transcriptions",
    response_model=TranscriptionResponse,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["audio"],
                        "properties": {
                            "audio": {"type": "string", "format": "binary"},
                            "language": {"type": "string", "default": "vie"},
                            "keyterms": {"type": "string"},
                        },
                    }
                }
            },
        }
    },
)
async def create_transcription(request: Request):
    request_id = getattr(request.state, "request_id", str(uuid4()))
    client_ip = getattr(request.state, "client_ip", "unknown")
    settings = get_settings()

    # 1. Service-to-service Authentication
    if not _is_authenticated(request, settings):
        logger.warning(
            "audit_event=transcription_auth_failed request_id=%s client_ip=%s",
            request_id,
            client_ip,
        )
        return _error(401, "unauthorized", "Invalid or missing service API key.", request_id)

    # 2. Rate Limiting (per client IP)
    allowed, remaining, retry_after = rate_limiter.is_allowed(
        client_ip, limit=settings.rate_limit_per_minute
    )
    if not allowed:
        logger.warning(
            "audit_event=transcription_rate_limited request_id=%s client_ip=%s limit=%d",
            request_id,
            client_ip,
            settings.rate_limit_per_minute,
        )
        headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
            "X-RateLimit-Remaining": "0",
        }
        return _error(
            429,
            "rate_limit_exceeded",
            f"Rate limit exceeded. Please retry in {retry_after} seconds.",
            request_id,
            headers=headers,
        )

    # 3. Payload validation. Explicit parsing applies the configured file limit,
    # avoiding Starlette's default 1 MB multipart-part limit.
    submission = await _read_submission(request, settings, request_id, client_ip)
    if isinstance(submission, JSONResponse):
        return submission

    if not settings.configured:
        logger.warning(
            "audit_event=transcription_rejected request_id=%s client_ip=%s reason=provider_not_configured",
            request_id,
            client_ip,
        )
        return _error(
            503,
            "provider_not_configured",
            "Speech transcription is not configured. Add the server API key and try again.",
            request_id,
        )

    resolved_keyterms = _resolve_keyterms(settings, submission.keyterms)
    if resolved_keyterms is None:
        logger.warning(
            "audit_event=transcription_rejected request_id=%s client_ip=%s reason=invalid_keyterms",
            request_id,
            client_ip,
        )
        return _error(
            400,
            "invalid_keyterms",
            "Use at most 1,000 keyterms, each no longer than 50 characters.",
            request_id,
        )

    logger.info(
        "audit_event=transcription_start request_id=%s client_ip=%s provider=%s model=%s audio_bytes=%d",
        request_id,
        client_ip,
        settings.stt_provider,
        settings.active_model_id,
        len(submission.audio_bytes),
    )

    transcribe_start = time.perf_counter()
    provider = _build_provider(settings)
    try:
        result = await provider.transcribe(
            audio_bytes=submission.audio_bytes,
            filename=submission.filename,
            content_type=submission.content_type or "application/octet-stream",
            language="vie",
            keyterms=resolved_keyterms,
        )
    except ProviderNoSpeech:
        logger.warning(
            "audit_event=transcription_failed request_id=%s client_ip=%s provider=%s reason=no_speech_detected",
            request_id,
            client_ip,
            settings.stt_provider,
        )
        return _error(422, "no_speech_detected", "No recognizable speech was found in this audio.", request_id)
    except ProviderRateLimited:
        logger.warning(
            "audit_event=transcription_failed request_id=%s client_ip=%s provider=%s reason=provider_rate_limited",
            request_id,
            client_ip,
            settings.stt_provider,
        )
        return _error(429, "provider_rate_limited", "The transcription provider is busy. Try again shortly.", request_id)
    except ProviderTimeout:
        logger.warning(
            "audit_event=transcription_failed request_id=%s client_ip=%s provider=%s reason=provider_timeout",
            request_id,
            client_ip,
            settings.stt_provider,
        )
        return _error(504, "provider_timeout", "Transcription took too long. Try a shorter recording.", request_id)
    except (ProviderUnavailable, ProviderError):
        logger.warning(
            "audit_event=transcription_failed request_id=%s client_ip=%s provider=%s reason=provider_error",
            request_id,
            client_ip,
            settings.stt_provider,
        )
        return _error(502, "provider_error", "The transcription provider could not process this audio.", request_id)

    duration_ms = round((time.perf_counter() - transcribe_start) * 1_000)
    logger.info(
        "audit_event=transcription_success request_id=%s client_ip=%s provider=%s model=%s duration_ms=%d",
        request_id,
        client_ip,
        settings.stt_provider,
        settings.active_model_id,
        duration_ms,
    )

    # 5. Optional: refine raw STT output with OpenAI (fail-open)
    final_text = result.text
    if settings.refine_enabled:
        assert settings.openai_api_key is not None
        final_text = await refine_text(
            result.text,
            openai_api_key=settings.openai_api_key.get_secret_value(),
            keyterms=resolved_keyterms,
            model=settings.openai_refine_model,
        )

    return TranscriptionResponse(
        request_id=request_id,
        text=final_text,
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
        model=settings.active_model_id,
    )


def _build_provider(settings: Settings) -> STTProvider:
    if settings.stt_provider == "elevenlabs":
        assert settings.elevenlabs_api_key is not None
        return ElevenLabsSTTProvider(
            api_key=settings.elevenlabs_api_key.get_secret_value(),
            model_id=settings.stt_model_id,
            timeout_seconds=settings.request_timeout_seconds,
        )
    if settings.stt_provider == "phowhisper":
        return PhoWhisperSTTProvider(
            model_id=settings.phowhisper_model_id,
            device=settings.phowhisper_device,
        )
    raise RuntimeError(f"Unsupported STT provider: {settings.stt_provider}")


def _is_supported_audio(filename: str, content_type: str) -> bool:
    return content_type in SUPPORTED_MIME_TYPES or Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS


async def _read_submission(
    request: Request,
    settings: Settings,
    request_id: str,
    client_ip: str,
) -> AudioSubmission | JSONResponse:
    """Read a single multipart upload and release it after copying its bytes."""
    try:
        form = await request.form(
            max_files=1,
            max_fields=2,
            max_part_size=settings.max_upload_bytes,
        )
    except StarletteHTTPException:
        logger.warning(
            "audit_event=transcription_rejected request_id=%s client_ip=%s reason=invalid_multipart",
            request_id,
            client_ip,
        )
        return _error(400, "invalid_multipart", "Send one audio file as multipart/form-data.", request_id)

    try:
        audio = form.get("audio")
        if audio is None:
            logger.warning(
                "audit_event=transcription_rejected request_id=%s client_ip=%s reason=missing_audio",
                request_id,
                client_ip,
            )
            return _error(400, "missing_audio", "Upload an audio file to transcribe.", request_id)
        if not isinstance(audio, StarletteUploadFile):
            return _error(400, "invalid_audio", "The audio field must be an uploaded file.", request_id)

        filename = audio.filename or "recording"
        content_type = (audio.content_type or "").lower()
        if not _is_supported_audio(filename, content_type):
            logger.warning(
                "audit_event=transcription_rejected request_id=%s client_ip=%s reason=unsupported_audio_type filename=%s",
                request_id,
                client_ip,
                filename,
            )
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
            logger.warning(
                "audit_event=transcription_rejected request_id=%s client_ip=%s reason=audio_too_large size_bytes=%d",
                request_id,
                client_ip,
                len(audio_bytes),
            )
            return _error(
                413,
                "audio_too_large",
                f"Audio must be no larger than {settings.max_upload_mb} MB.",
                request_id,
            )

        language = form.get("language", "vie")
        if not isinstance(language, str):
            return _error(400, "invalid_language", "Language must be text.", request_id)
        normalized_language = language.strip().lower() or "vie"
        if normalized_language not in {"vi", "vie", "vi-vn"}:
            return _error(400, "unsupported_language", "This POC supports Vietnamese only.", request_id)

        keyterms = form.get("keyterms")
        if keyterms is not None and not isinstance(keyterms, str):
            return _error(400, "invalid_keyterms", "Keyterms must be comma-separated text.", request_id)

        return AudioSubmission(
            audio_bytes=audio_bytes,
            filename=filename,
            content_type=content_type,
            language=normalized_language,
            keyterms=keyterms,
        )
    finally:
        await form.close()


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


def _is_authenticated(request: Request, settings: Settings) -> bool:
    if not settings.is_auth_enabled:
        return True

    assert settings.service_api_key is not None
    expected = settings.service_api_key.get_secret_value().strip()

    # 1. Check x-api-key header
    api_key = request.headers.get("x-api-key")
    if api_key and secrets.compare_digest(api_key.strip(), expected):
        return True

    # 2. Check Authorization: Bearer <token>
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.strip().split(maxsplit=1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            if secrets.compare_digest(parts[1].strip(), expected):
                return True

    return False


def _error(
    status_code: int,
    code: str,
    message: str,
    request_id: str,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"request_id": request_id, "error": {"code": code, "message": message}},
        headers=headers,
    )
