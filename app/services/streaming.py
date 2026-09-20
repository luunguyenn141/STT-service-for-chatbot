"""Bounded PCM buffering and origin-bound short-lived WebSocket tickets."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import math
import secrets
import struct
import time
import wave
from collections import deque
from typing import Literal

from app.config import Settings

SAMPLE_RATE = 16_000
FRAME_BYTES = 640  # 20 ms, mono signed PCM16 little endian
TICKET_TTL = 60
_development_secret = secrets.token_bytes(32)


def _signing_key(settings: Settings) -> bytes:
    for secret in (settings.stream_token_secret, settings.service_api_key):
        if secret and secret.get_secret_value().strip():
            return secret.get_secret_value().strip().encode()
    return _development_secret


EndpointingMode = Literal["silence", "manual"]


def issue_ticket(
    settings: Settings,
    origin: str,
    keyterms: list[str] | None = None,
    endpointing: EndpointingMode = "silence",
) -> str:
    claims = {
        "origin": origin,
        "exp": int(time.time()) + TICKET_TTL,
        "nonce": secrets.token_hex(16),
        "keyterms": (keyterms or [])[:20],
        "endpointing": endpointing,
    }
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    signature = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_ticket(settings: Settings, ticket: str, origin: str) -> dict | None:
    try:
        if not isinstance(ticket, str) or len(ticket) > 2048:
            return None
        payload, signature = ticket.split(".")
        expected = hmac.new(_signing_key(settings), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return None
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        keyterms = claims.get("keyterms", [])
        if not isinstance(keyterms, list) or not all(isinstance(term, str) for term in keyterms):
            return None
        endpointing = claims.get("endpointing", "silence")
        if endpointing not in {"silence", "manual"}:
            return None
        claims["endpointing"] = endpointing
        if claims["origin"] != origin or not time.time() < claims["exp"] <= time.time() + TICKET_TTL + 1:
            return None
        return claims
    except (ValueError, KeyError, TypeError):
        return None


def verify_ticket(settings: Settings, ticket: str, origin: str) -> bool:
    return read_ticket(settings, ticket, origin) is not None


def pcm_to_wav(pcm: bytes) -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(pcm)
    return out.getvalue()


class UtteranceBuffer:
    """Energy VAD with pre-roll; one bounded utterance per mic activation.

    This is a tunable baseline for a quiet microphone, not neural VAD. PCM is
    evaluated in 20 ms frames regardless of network message boundaries.
    """

    def __init__(self, settings: Settings, endpointing: EndpointingMode = "silence"):
        self.settings = settings
        self.endpointing = endpointing
        self.pending = bytearray()
        self.audio = bytearray()
        self.preroll: deque[bytes] = deque(maxlen=10)
        self.voiced_ms = 0
        self.silent_ms = 0
        self.total_bytes = 0
        self.reason: str | None = None

    @property
    def has_speech(self) -> bool:
        return self.voiced_ms >= 200

    def feed(self, data: bytes) -> None:
        self.pending.extend(data)
        self.total_bytes += len(data)
        while len(self.pending) >= FRAME_BYTES and self.reason is None:
            frame = bytes(self.pending[:FRAME_BYTES])
            del self.pending[:FRAME_BYTES]
            samples = struct.unpack("<320h", frame)
            rms = math.sqrt(sum(value * value for value in samples) / len(samples)) / 32768
            voiced = rms >= self.settings.stream_vad_threshold
            if not self.audio:
                if not voiced:
                    self.preroll.append(frame)
                    continue
                self.audio.extend(b"".join(self.preroll))
                self.preroll.clear()
            self.audio.extend(frame)
            self.voiced_ms += 20 if voiced else 0
            self.silent_ms = 0 if voiced else self.silent_ms + 20
            if self.silent_ms >= self.settings.stream_silence_ms:
                if self.has_speech and self.endpointing == "silence":
                    self.reason = "silence"
                elif not self.has_speech:
                    # A click/pop must not end the user's recording.
                    self.audio.clear()
                    self.voiced_ms = self.silent_ms = 0
            if len(self.audio) >= self.settings.stream_max_audio_seconds * SAMPLE_RATE * 2:
                self.reason = "max_duration"
        if self.total_bytes >= 30 * SAMPLE_RATE * 2 and self.reason is None:
            self.reason = "max_duration"

    def finish(self) -> None:
        if self.audio and self.pending:
            self.audio.extend(self.pending)
            self.pending.clear()
        self.reason = self.reason or "stop"
