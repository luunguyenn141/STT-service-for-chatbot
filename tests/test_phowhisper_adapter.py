from __future__ import annotations

import pytest

from app.services.stt.base import ProviderNoSpeech, ProviderUnavailable
from app.services.stt.phowhisper import PhoWhisperSTTProvider


@pytest.mark.asyncio
async def test_phowhisper_transcribes_with_the_selected_model_and_device():
    captured: dict[str, object] = {}

    def loader(model_id: str, device: int):
        captured["model_id"] = model_id
        captured["device"] = device

        def transcribe(audio_bytes: bytes):
            captured["audio_bytes"] = audio_bytes
            return {"text": "  Xin chao MSB  "}

        return transcribe

    provider = PhoWhisperSTTProvider(
        model_id="vinai/PhoWhisper-base",
        device=0,
        pipeline_loader=loader,
    )

    result = await provider.transcribe(
        audio_bytes=b"audio-data",
        filename="demo.wav",
        content_type="audio/wav",
        language="vie",
        keyterms=["MSB"],
    )

    assert captured == {
        "model_id": "vinai/PhoWhisper-base",
        "device": 0,
        "audio_bytes": b"audio-data",
    }
    assert result.text == "Xin chao MSB"
    assert result.language_code == "vie"
    assert result.words == []


@pytest.mark.asyncio
async def test_phowhisper_maps_empty_transcript_to_no_speech():
    provider = PhoWhisperSTTProvider(
        model_id="test-model",
        pipeline_loader=lambda *_: lambda _: {"text": "  "},
    )

    with pytest.raises(ProviderNoSpeech):
        await provider.transcribe(
            audio_bytes=b"audio-data",
            filename="demo.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )


@pytest.mark.asyncio
async def test_phowhisper_maps_runtime_failures_to_provider_unavailable():
    def loader(*_):
        raise FileNotFoundError("ffmpeg")

    provider = PhoWhisperSTTProvider(model_id="test-model", pipeline_loader=loader)

    with pytest.raises(ProviderUnavailable):
        await provider.transcribe(
            audio_bytes=b"audio-data",
            filename="demo.wav",
            content_type="audio/wav",
            language="vie",
            keyterms=[],
        )