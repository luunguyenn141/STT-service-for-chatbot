from app.api.transcriptions import _build_provider
from app.config import Settings
from app.services.stt.elevenlabs import ElevenLabsSTTProvider
from app.services.stt.phowhisper import PhoWhisperSTTProvider
from app.services.stt.vbee import VbeeSTTProvider


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


def test_vbee_provider_is_selected_with_its_credentials():
    settings = Settings(
        stt_provider="vbee",
        vbee_api_token="test-token",
        vbee_app_id="test-app-id",
    )

    provider = _build_provider(settings)

    assert settings.configured is True
    assert settings.active_model_id == "vbee-stt"
    assert isinstance(provider, VbeeSTTProvider)


def test_vbee_requires_both_token_and_app_id():
    assert Settings(
        stt_provider="vbee", vbee_api_token="test-token", vbee_app_id=""
    ).configured is False
