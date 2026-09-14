from __future__ import annotations

import json

import pytest

from app.services.stt.base import ProviderNoSpeech, ProviderUnavailable
from app.services.stt.phowhisper import PhoWhisperSTTProvider, _model_source


def test_matching_bundled_model_uses_local_directory(monkeypatch, tmp_path):
    (tmp_path / "bundle.json").write_text(json.dumps({"model_id": "vinai/PhoWhisper-base"}))
    monkeypatch.setenv("PHOWHISPER_BUNDLE_DIR", str(tmp_path))
    assert _model_source("vinai/PhoWhisper-base") == str(tmp_path)


def test_bundled_model_does_not_override_a_different_model(monkeypatch, tmp_path):
    (tmp_path / "bundle.json").write_text(json.dumps({"model_id": "vinai/PhoWhisper-base"}))
    monkeypatch.setenv("PHOWHISPER_BUNDLE_DIR", str(tmp_path))
    assert _model_source("vinai/PhoWhisper-small") == "vinai/PhoWhisper-small"


def test_image_without_bundle_keeps_normal_model_loading(monkeypatch, tmp_path):
    monkeypatch.setenv("PHOWHISPER_BUNDLE_DIR", str(tmp_path))
    assert _model_source("vinai/PhoWhisper-base") == "vinai/PhoWhisper-base"


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
