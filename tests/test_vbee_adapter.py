from __future__ import annotations

from io import BytesIO
import wave

import httpx
import pytest

from app.services.stt.base import ProviderAuthenticationFailed, ProviderInvalidAudio, ProviderRateLimited
from app.services.stt.vbee import VBEE_STT_ENDPOINT, VbeeSTTProvider


@pytest.mark.asyncio
async def test_short_request_prefers_sync_mode(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = request.content
        return httpx.Response(
            200,
            json={
                "transcriptId": "job-123",
                "status": "COMPLETED",
                "transcript": "tôi muốn kiểm tra số dư",
                "utterances": [],
            },
        )

    provider = VbeeSTTProvider(
        api_token="secret-token",
        app_id="bank-app",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 3.0

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    result = await provider.transcribe(
        audio_bytes=b"source-audio",
        filename="voice.webm",
        content_type="audio/webm",
        language="vie",
        keyterms=["MSB"],
    )

    assert result.text == "tôi muốn kiểm tra số dư"
    assert result.language_code == "vie"
    assert result.words == []
    assert captured["headers"]["authorization"] == "Bearer secret-token"
    assert captured["headers"]["app-id"] == "bank-app"
    assert b'name="mode"' in captured["body"]
    assert b"\r\nsync\r\n" in captured["body"]
    assert b'name="audioContent"' in captured["body"]


@pytest.mark.asyncio
async def test_batch_request_polls_until_complete(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"transcriptId": "job-456", "status": "PENDING"})
        return httpx.Response(
            200,
            json={"transcriptId": "job-456", "status": "COMPLETED", "transcript": "nội dung dài"},
        )

    provider = VbeeSTTProvider(
        api_token="token",
        app_id="app-id",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 12.0

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    monkeypatch.setattr("app.services.stt.vbee.asyncio.sleep", no_sleep)

    result = await provider.transcribe(
        audio_bytes=b"audio",
        filename="voice.mp3",
        content_type="audio/mpeg",
        language="vie",
        keyterms=[],
    )

    assert result.text == "nội dung dài"
    assert [request.method for request in requests] == ["POST", "GET"]
    assert requests[0].url == httpx.URL(VBEE_STT_ENDPOINT)
    assert requests[1].url == httpx.URL(f"{VBEE_STT_ENDPOINT}/transcripts/job-456")
    assert b"async" in requests[0].content


@pytest.mark.asyncio
async def test_short_request_falls_back_to_batch_when_sync_feature_is_unavailable(monkeypatch):
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and b"\r\nsync\r\n" in request.content:
            return httpx.Response(403, json={"error": {"code": "FORBIDDEN", "message": "Missing feature: stt-sync"}})
        if request.method == "POST":
            return httpx.Response(200, json={"transcriptId": "job-fallback", "status": "PENDING"})
        return httpx.Response(200, json={"transcriptId": "job-fallback", "status": "COMPLETED", "transcript": "số dư của tôi"})

    provider = VbeeSTTProvider(api_token="token", app_id="app-id", timeout_seconds=5, transport=httpx.MockTransport(handler))

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 3.0

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    monkeypatch.setattr("app.services.stt.vbee.asyncio.sleep", no_sleep)
    result = await provider.transcribe(audio_bytes=b"audio", filename="voice.wav", content_type="audio/wav", language="vie", keyterms=[])

    assert result.text == "số dư của tôi"
    assert [request.method for request in requests] == ["POST", "POST", "GET"]
    assert b"\r\nsync\r\n" in requests[0].content
    assert b"\r\nasync\r\n" in requests[1].content


@pytest.mark.asyncio
async def test_vbee_rate_limit_is_mapped(monkeypatch):
    provider = VbeeSTTProvider(
        api_token="token",
        app_id="app-id",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(429)),
    )

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 1.0

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    with pytest.raises(ProviderRateLimited):
        await provider.transcribe(
            audio_bytes=b"audio",
            filename="voice.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )


@pytest.mark.asyncio
async def test_vbee_auth_failure_is_distinct_and_bearer_prefix_is_normalized(monkeypatch):
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers["authorization"]
        return httpx.Response(401)

    provider = VbeeSTTProvider(
        api_token="Bearer token",
        app_id="app-id",
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 1.0

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    with pytest.raises(ProviderAuthenticationFailed):
        await provider.transcribe(
            audio_bytes=b"audio",
            filename="voice.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )

    assert captured["authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_vbee_bad_request_is_reported_as_invalid_audio(monkeypatch):
    provider = VbeeSTTProvider(
        api_token="token",
        app_id="app-id",
        timeout_seconds=5,
        transport=httpx.MockTransport(lambda _: httpx.Response(400)),
    )

    async def fake_wav(_: bytes, __: float) -> tuple[bytes, float]:
        return b"wav-data", 1.0

    monkeypatch.setattr(provider, "_to_wav", fake_wav)
    with pytest.raises(ProviderInvalidAudio):
        await provider.transcribe(
            audio_bytes=b"audio",
            filename="voice.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )


@pytest.mark.asyncio
async def test_canonical_pfm_wav_skips_ffmpeg(monkeypatch):
    with BytesIO() as output:
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(16_000)
            wav.writeframes(b"\x00\x00" * 8_000)
        source = output.getvalue()

    async def unexpected_ffmpeg(*_args, **_kwargs):
        raise AssertionError("canonical WAV must not start ffmpeg")

    monkeypatch.setattr("app.services.stt.vbee.asyncio.create_subprocess_exec", unexpected_ffmpeg)
    provider = VbeeSTTProvider(api_token="token", app_id="app-id", timeout_seconds=5)
    converted, duration = await provider._to_wav(source, float("inf"))

    assert converted is source
    assert duration == 0.5
