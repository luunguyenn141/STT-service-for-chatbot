from app.api.transcriptions import _build_provider
from app.config import Settings
from app.services.stt.elevenlabs import ElevenLabsSTTProvider
from app.services.stt.phowhisper import PhoWhisperSTTProvider


def test_elevenlabs_provider_is_selected_by_default():
    provider = _build_provider(Settings(stt_provider="elevenlabs", elevenlabs_api_key="test-key"))

    assert isinstance(provider, ElevenLabsSTTProvider)


def test_phowhisper_provider_is_selected_without_an_elevenlabs_key():
    settings = Settings(
        stt_provider="phowhisper",
        phowhisper_model_id="vinai/PhoWhisper-small",
        phowhisper_device=0,
        elevenlabs_api_key=""
    )

    provider = _build_provider(settings)

    assert settings.configured is True
    assert settings.active_model_id == "vinai/PhoWhisper-small"
    assert isinstance(provider, PhoWhisperSTTProvider)