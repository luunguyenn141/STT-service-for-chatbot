from __future__ import annotations

import asyncio
from contextlib import suppress
import json
import time
from typing import Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from app.api.transcriptions import _build_provider, _is_authenticated
from app.config import get_settings
from app.services.rate_limiter import rate_limiter
from app.services.streaming import SAMPLE_RATE, TICKET_TTL, UtteranceBuffer, issue_ticket, pcm_to_wav, read_ticket
from app.services.stt.base import ProviderError, ProviderNoSpeech
from app.services.transcript_refiner import refine_transcript

router = APIRouter(prefix="/api/v1", tags=["streaming"])
_active_sessions = 0


class SessionRequest(BaseModel):
    origin: str = Field(max_length=512)
    keyterms: list[str] = Field(default_factory=list, max_length=20)
    endpointing: Literal["silence", "manual"] = "silence"

    @field_validator("origin")
    @classmethod
    def valid_origin(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme not in {"http", "https"} or not parts.netloc or parts.path
                or parts.query or parts.fragment or parts.username or parts.password):
            raise ValueError("Expected a browser origin, e.g. https://chat.example.com")
        return value

    @field_validator("keyterms")
    @classmethod
    def valid_keyterms(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        total_bytes = 0
        for value in values:
            term = " ".join(value.strip().split())
            if not term or len(term) > 64 or any(ord(char) < 32 for char in term):
                raise ValueError("Each keyterm must be a single non-empty line of at most 64 characters.")
            key = term.casefold()
            if key not in seen:
                encoded_size = len(term.encode("utf-8"))
                if total_bytes + encoded_size > 640:
                    break
                seen.add(key)
                cleaned.append(term)
                total_bytes += encoded_size
        return cleaned


@router.post("/stream-sessions")
async def create_session(body: SessionRequest, request: Request):
    settings = get_settings()
    if not _is_authenticated(request, settings):
        raise HTTPException(401, "Invalid or missing service API key.")
    peer = request.client.host if request.client else "unknown"
    allowed, _, _ = rate_limiter.is_allowed(f"stream-ticket:{peer}", limit=30)
    if not allowed:
        raise HTTPException(429, "Too many streaming sessions.")
    return JSONResponse(
        {"token": issue_ticket(settings, body.origin, body.keyterms, body.endpointing), "expires_in": TICKET_TTL,
         "sample_rate": SAMPLE_RATE, "format": "pcm_s16le", "channels": 1},
        headers={"Cache-Control": "no-store"},
    )


async def _error(ws: WebSocket, code: str, message: str, close_code: int = 1008):
    await ws.send_json({"type": "error", "code": code, "message": message})
    await ws.close(code=close_code)


@router.websocket("/transcriptions/stream")
async def stream_transcription(ws: WebSocket):
    global _active_sessions
    settings = get_settings()
    await ws.accept()
    # The ticket is in the first message, never a URL/query parameter in logs.
    try:
        message = await asyncio.wait_for(ws.receive_text(), timeout=5)
        if len(message) > 4096:
            raise ValueError
        start = json.loads(message)
        if not isinstance(start, dict) or start.get("type") != "start":
            raise ValueError
        claims = read_ticket(settings, start.get("token", ""), ws.headers.get("origin", ""))
        if claims is None:
            await _error(ws, "unauthorized", "Phiên ghi âm hết hạn. Vui lòng thử lại.")
            return
    except (ValueError, TypeError, KeyError, asyncio.TimeoutError):
        await _error(ws, "invalid_start", "Không khởi tạo được phiên ghi âm.")
        return
    except WebSocketDisconnect:
        return
    if _active_sessions >= settings.stream_max_sessions:
        await _error(ws, "busy", "Dịch vụ đang bận. Vui lòng thử lại.", 1013)
        return

    _active_sessions += 1
    buffer = UtteranceBuffer(settings, claims["endpointing"])
    changed = asyncio.Event()
    provider = _build_provider(settings)
    started = time.monotonic()
    last_partial_size = 0
    session_keyterms = list(dict.fromkeys(settings.configured_keyterms + claims.get("keyterms", [])))

    async def receive_audio():
        nonlocal last_partial_size
        while buffer.reason is None:
            packet = await asyncio.wait_for(ws.receive(), timeout=10)
            if packet["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(packet.get("code", 1000))
            data = packet.get("bytes")
            if data is not None:
                if not data or len(data) % 2 or len(data) > 16_000:
                    raise ValueError("Audio phải là PCM16 mono 16 kHz, tối đa 500 ms mỗi gói.")
                # Limit uploads running substantially faster than a live mic.
                if buffer.total_bytes + len(data) > (time.monotonic() - started + 2) * SAMPLE_RATE * 2:
                    raise ValueError("Audio được gửi quá nhanh. Hãy bắt đầu phiên mới.")
                buffer.feed(data)
            else:
                text = packet.get("text", "")
                if len(text) > 128 or json.loads(text) != {"type": "stop"}:
                    raise ValueError("Lệnh ghi âm không hợp lệ.")
                buffer.finish()
            if buffer.reason:
                await ws.send_json({"type": "finishing", "reason": buffer.reason})
                changed.set()
                return
            if buffer.has_speech and len(buffer.audio) - last_partial_size >= settings.stream_partial_seconds * SAMPLE_RATE * 2:
                last_partial_size = len(buffer.audio)
                if settings.stt_provider == "phowhisper":
                    changed.set()

    async def recognize():
        previous = ""
        while True:
            await changed.wait()
            changed.clear()
            final = buffer.reason is not None
            snapshot = bytes(buffer.audio)
            text = ""
            if buffer.has_speech:
                try:
                    result = await asyncio.wait_for(provider.transcribe(
                        audio_bytes=pcm_to_wav(snapshot), filename="stream.wav", content_type="audio/wav",
                        language="vie", keyterms=session_keyterms,
                    ), timeout=settings.request_timeout_seconds)
                    text = result.text
                except ProviderNoSpeech:
                    text = ""
            if final:
                # Refine the final text with OpenAI if configured (fail-open)
                raw_text = text
                refinement_status = "disabled"
                if text and settings.refine_enabled:
                    assert settings.openai_api_key is not None
                    refinement = await refine_transcript(
                        text,
                        openai_api_key=settings.openai_api_key.get_secret_value(),
                        keyterms=session_keyterms,
                        model=settings.openai_refine_model,
                        timeout_seconds=settings.stt_refine_timeout_seconds,
                        max_chars=settings.stt_refine_max_chars,
                        normalize_money=settings.stt_refine_money,
                        money_separator=settings.stt_refine_money_separator,
                        name_case=settings.stt_refine_name_case,
                        names=settings.stt_refine_names,
                        term_aliases=settings.stt_refine_term_aliases,
                        entities=settings.stt_refine_entities,
                        min_similarity=settings.stt_refine_min_similarity,
                    )
                    text = refinement.text
                    refinement_status = refinement.status
                await ws.send_json({
                    "type": "final",
                    "text": text,
                    "raw_text": raw_text,
                    "refined": text != raw_text,
                    "refinement_status": refinement_status,
                    "reason": buffer.reason,
                })
                return
            # If the final snapshot is ready, skip a stale partial and process it.
            if buffer.reason is None and text and text != previous:
                previous = text
                await ws.send_json({"type": "partial", "text": text})

    tasks: list[asyncio.Task] = []
    try:
        await ws.send_json({"type": "ready", "sample_rate": SAMPLE_RATE, "format": "pcm_s16le", "channels": 1})
        tasks = [asyncio.create_task(receive_audio()), asyncio.create_task(recognize())]
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=30 + settings.request_timeout_seconds * 2)
        await ws.close(code=1000)
    except WebSocketDisconnect:
        pass
    except asyncio.TimeoutError:
        await _error(ws, "timeout", "Phiên ghi âm đã hết thời gian. Vui lòng thử lại.", 1011)
    except (ValueError, TypeError, KeyError):
        await _error(ws, "invalid_audio", "Dữ liệu ghi âm không hợp lệ.")
    except ProviderError:
        await _error(ws, "provider_error", "Không nhận dạng được âm thanh. Vui lòng thử lại.", 1011)
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError, Exception):
                await task
        _active_sessions -= 1
