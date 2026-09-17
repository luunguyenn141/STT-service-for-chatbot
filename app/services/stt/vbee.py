from __future__ import annotations

import asyncio
from io import BytesIO
import time
import wave

import httpx

from app.services.stt.base import (
    ProviderAuthenticationFailed,
    ProviderInvalidAudio,
    ProviderNoSpeech,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTranscription,
    ProviderUnavailable,
    ProviderUnexpectedResponse,
    STTProvider,
)


VBEE_STT_ENDPOINT = "https://api.vbee.vn/v1/stt"
SAMPLE_RATE = 16_000
MAX_PCM_BYTES = 94_000_000  # Leave room for the WAV header under Vbee's 100 MB batch limit.
POLL_INTERVAL_SECONDS = 2.0


class _SyncFeatureUnavailable(Exception):
    pass


class VbeeSTTProvider(STTProvider):
    """Vbee adapter preferring low-latency full-audio sync with batch fallback."""

    def __init__(
        self,
        *,
        api_token: str,
        app_id: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        token = api_token.strip()
        # Accept either the raw JWT or a value copied from an Authorization
        # header. The request itself adds exactly one Bearer prefix.
        self._api_token = token[7:].strip() if token.lower().startswith("bearer ") else token
        self._app_id = app_id
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
        # Vbee's documented STT endpoint accepts WAV; its API has no language or
        # keyterm parameters. Keep those inputs in the shared service interface.
        del filename, content_type, language, keyterms

        deadline = time.monotonic() + self._timeout_seconds
        wav_bytes, duration = await self._to_wav(audio_bytes, deadline)
        headers = {
            "Authorization": f"Bearer {self._api_token}",
            "App-Id": self._app_id,
        }

        async with httpx.AsyncClient(transport=self._transport) as client:
            mode = "sync" if duration < 10 else "async"
            try:
                payload = await self._submit(client, headers, wav_bytes, mode, deadline)
            except _SyncFeatureUnavailable:
                # Some Vbee applications only have `stt-async`. Preserve a
                # working service while allowing `stt-sync` to take effect as
                # soon as Vbee enables that feature for the same App ID.
                payload = await self._submit(client, headers, wav_bytes, "async", deadline)
            while payload.get("status") in {"PENDING", "PROCESSING"}:
                transcript_id = payload.get("transcriptId")
                if not isinstance(transcript_id, str) or not transcript_id:
                    raise ProviderUnexpectedResponse
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ProviderTimeout
                await asyncio.sleep(min(POLL_INTERVAL_SECONDS, remaining))
                payload = await self._request(
                    client,
                    "GET",
                    f"{VBEE_STT_ENDPOINT}/transcripts/{transcript_id}",
                    headers=headers,
                    deadline=deadline,
                )

        if payload.get("status") == "FAILED":
            raise ProviderUnavailable
        if payload.get("status") != "COMPLETED":
            raise ProviderUnexpectedResponse
        transcript = payload.get("transcript")
        if not isinstance(transcript, str):
            raise ProviderUnexpectedResponse
        if not transcript.strip():
            raise ProviderNoSpeech
        # Vbee documents utterance timestamps, not word-level timings.
        return ProviderTranscription(text=transcript.strip(), language_code="vie")

    async def _submit(
        self,
        client: httpx.AsyncClient,
        headers: dict[str, str],
        wav_bytes: bytes,
        mode: str,
        deadline: float,
    ) -> dict[str, object]:
        return await self._request(
            client,
            "POST",
            VBEE_STT_ENDPOINT,
            headers=headers,
            files={"audioContent": ("recording.wav", wav_bytes, "audio/wav")},
            data={"mode": mode},
            deadline=deadline,
        )

    async def _to_wav(self, audio_bytes: bytes, deadline: float) -> tuple[bytes, float]:
        # PFM already records canonical 16 kHz mono PCM WAV. Forward it as-is to
        # avoid starting ffmpeg on every request; keep ffmpeg for other clients.
        canonical = _canonical_wav(audio_bytes)
        if canonical is not None:
            return canonical

        try:
            process = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-nostdin",
                "-hide_banner",
                "-loglevel", "error",
                "-i", "pipe:0",
                "-vn",
                "-ac", "1",
                "-ar", str(SAMPLE_RATE),
                "-c:a", "pcm_s16le",
                "-f", "s16le",
                "-fs", str(MAX_PCM_BYTES),
                "pipe:1",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except OSError as exc:
            raise ProviderUnavailable from exc

        try:
            pcm_bytes, _ = await asyncio.wait_for(
                process.communicate(audio_bytes), timeout=_remaining(deadline)
            )
        except asyncio.TimeoutError as exc:
            process.kill()
            await process.communicate()
            raise ProviderTimeout from exc

        if process.returncode != 0 or not pcm_bytes or len(pcm_bytes) >= MAX_PCM_BYTES:
            raise ProviderInvalidAudio

        with BytesIO() as output:
            with wave.open(output, "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(SAMPLE_RATE)
                wav.writeframes(pcm_bytes)
            return output.getvalue(), len(pcm_bytes) / (SAMPLE_RATE * 2)

    async def _request(
        self,
        client: httpx.AsyncClient,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        deadline: float,
        **kwargs: object,
    ) -> dict[str, object]:
        try:
            response = await client.request(
                method,
                url,
                headers=headers,
                timeout=_remaining(deadline),
                **kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeout from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailable from exc

        if response.status_code == 429:
            raise ProviderRateLimited
        if response.status_code == 403 and _missing_sync_feature(response):
            raise _SyncFeatureUnavailable
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationFailed
        if response.status_code >= 500:
            raise ProviderUnavailable
        if response.status_code == 400:
            raise ProviderInvalidAudio
        if response.is_error:
            raise ProviderUnexpectedResponse
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderUnexpectedResponse from exc
        if not isinstance(payload, dict):
            raise ProviderUnexpectedResponse
        return payload


def _missing_sync_feature(response: httpx.Response) -> bool:
    try:
        payload = response.json()
    except ValueError:
        return False
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    return isinstance(error, dict) and "stt-sync" in str(error.get("message", "")).lower()


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProviderTimeout
    return remaining


def _canonical_wav(audio_bytes: bytes) -> tuple[bytes, float] | None:
    if not audio_bytes or len(audio_bytes) >= MAX_PCM_BYTES + 44:
        return None
    try:
        with wave.open(BytesIO(audio_bytes), "rb") as wav:
            if (
                wav.getnchannels() != 1
                or wav.getsampwidth() != 2
                or wav.getframerate() != SAMPLE_RATE
                or wav.getcomptype() != "NONE"
            ):
                return None
            frame_count = wav.getnframes()
            if frame_count <= 0 or len(wav.readframes(frame_count)) != frame_count * 2:
                return None
    except (EOFError, wave.Error):
        return None
    return audio_bytes, frame_count / SAMPLE_RATE
