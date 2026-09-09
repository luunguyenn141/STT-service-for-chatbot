from __future__ import annotations

import httpx
import pytest

from app.services.stt.base import ProviderRateLimited, ProviderTimeout
from app.services.stt.elevenlabs import ElevenLabsSTTProvider


@pytest.mark.asyncio
async def test_adapter_sends_expected_multipart_fields():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["xi-api-key"] == "test-key"
        assert b"scribe_v2" in request.content
        assert b"MSB" in request.content
        assert b"demo.wav" in request.content
        return httpx.Response(
            200,
            json={
                "text": "Xin chào",
                "language_code": "vie",
                "language_probability": 0.98,
                "words": [{"text": "Xin", "start": 0, "end": 0.2, "logprob": -0.1}],
            },
        )

    provider = ElevenLabsSTTProvider(
        api_key="test-key",
        model_id="scribe_v2",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )

    result = await provider.transcribe(
        audio_bytes=b"audio-data",
        filename="demo.wav",
        content_type="audio/wav",
        language="vie",
        keyterms=["MSB"],
    )

    assert result.text == "Xin chào"
    assert result.words[0].text == "Xin"


@pytest.mark.asyncio
async def test_adapter_maps_rate_limit():
    provider = ElevenLabsSTTProvider(
        api_key="test-key",
        model_id="scribe_v2",
        timeout_seconds=10,
        transport=httpx.MockTransport(lambda _: httpx.Response(429)),
    )

    with pytest.raises(ProviderRateLimited):
        await provider.transcribe(
            audio_bytes=b"audio-data",
            filename="demo.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )


@pytest.mark.asyncio
async def test_adapter_maps_request_timeout():
    async def handler(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    provider = ElevenLabsSTTProvider(
        api_key="test-key",
        model_id="scribe_v2",
        timeout_seconds=10,
        transport=httpx.MockTransport(handler),
    )

    with pytest.raises(ProviderTimeout):
        await provider.transcribe(
            audio_bytes=b"audio-data",
            filename="demo.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )
