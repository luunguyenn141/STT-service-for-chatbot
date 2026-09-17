from __future__ import annotations

from dataclasses import dataclass

from fastapi.testclient import TestClient

from app.api import transcriptions
from app.config import get_settings
from app.main import app
from app.services.stt.base import (
    ProviderAuthenticationFailed,
    ProviderInvalidAudio,
    ProviderRateLimited,
    ProviderTimeout,
    ProviderTranscription,
    ProviderWord,
)


@dataclass
class StubProvider:
    result: ProviderTranscription | Exception
    captured_keyterms: list[str] | None = None

    async def transcribe(self, **kwargs):
        self.captured_keyterms = kwargs["keyterms"]
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _client_with_provider(monkeypatch, provider: StubProvider, **environment: str) -> TestClient:
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    get_settings.cache_clear()
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: provider)
    return TestClient(app)


def _success_result() -> ProviderTranscription:
    return ProviderTranscription(
        text="Tôi muốn kiểm tra số dư tài khoản",
        language_code="vie",
        language_probability=0.99,
        words=[ProviderWord(text="Tôi", start=0.0, end=0.2, logprob=-0.03)],
    )


def test_valid_audio_maps_provider_response(monkeypatch):
    provider = StubProvider(_success_result())
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("demo.wav", b"not-a-real-wav", "audio/wav")},
            data={"keyterms": "MSB, thẻ tín dụng"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["text"] == "Tôi muốn kiểm tra số dư tài khoản"
    assert payload["language_code"] == "vie"
    assert payload["words"][0]["logprob"] == -0.03
    assert provider.captured_keyterms == ["MSB", "thẻ tín dụng"]
    get_settings.cache_clear()


def test_missing_audio_returns_validation_error(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.post("/api/v1/transcriptions")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "missing_audio"
    get_settings.cache_clear()


def test_unsupported_audio_returns_safe_error(monkeypatch):
    provider = StubProvider(_success_result())
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("notes.txt", b"not audio", "text/plain")},
        )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_audio_type"
    get_settings.cache_clear()


def test_oversized_audio_returns_413(monkeypatch):
    provider = StubProvider(_success_result())
    with _client_with_provider(monkeypatch, provider, MAX_UPLOAD_MB="1") as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("large.wav", b"a" * (1024 * 1024 + 1), "audio/wav")},
        )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "audio_too_large"
    get_settings.cache_clear()


def test_provider_timeout_is_sanitized(monkeypatch):
    provider = StubProvider(ProviderTimeout("internal upstream detail"))
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("demo.wav", b"audio", "audio/wav")},
        )

    assert response.status_code == 504
    assert "internal upstream detail" not in response.text
    get_settings.cache_clear()


def test_provider_rate_limit_maps_to_429(monkeypatch):
    provider = StubProvider(ProviderRateLimited())
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("demo.wav", b"audio", "audio/wav")},
        )

    assert response.status_code == 429
    assert response.json()["error"]["code"] == "provider_rate_limited"
    get_settings.cache_clear()


def test_provider_auth_failure_returns_safe_specific_code(monkeypatch):
    provider = StubProvider(ProviderAuthenticationFailed("upstream credential detail"))
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("demo.wav", b"audio", "audio/wav")},
        )

    assert response.status_code == 502
    assert response.json()["error"]["code"] == "provider_authentication_failed"
    assert "upstream credential detail" not in response.text
    get_settings.cache_clear()


def test_provider_invalid_audio_maps_to_400(monkeypatch):
    provider = StubProvider(ProviderInvalidAudio())
    with _client_with_provider(monkeypatch, provider) as client:
        response = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("demo.wav", b"audio", "audio/wav")},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_audio"
    get_settings.cache_clear()
