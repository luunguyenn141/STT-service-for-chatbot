from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api import transcriptions
from app.config import get_settings
from app.main import app
from app.services.rate_limiter import rate_limiter
from app.services.stt.base import ProviderTranscription, ProviderWord


def _mock_success_provider():
    class DummyProvider:
        async def transcribe(self, **kwargs):
            return ProviderTranscription(
                text="Chuyển tiền cho Nam 500k",
                language_code="vie",
                language_probability=0.99,
                words=[ProviderWord(text="Chuyển", start=0.0, end=0.3)],
            )

    return DummyProvider()


@pytest.fixture(autouse=True)
def reset_state():
    rate_limiter.reset()
    get_settings.cache_clear()
    yield
    rate_limiter.reset()
    get_settings.cache_clear()


def test_auth_not_configured_allows_access(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.delenv("SERVICE_API_KEY", raising=False)
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
        )
        assert res.status_code == 200
        assert res.json()["text"] == "Chuyển tiền cho Nam 500k"


def test_auth_rejects_missing_key_when_configured(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("SERVICE_API_KEY", "secret-bank-token")
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthorized"


def test_auth_rejects_invalid_key_when_configured(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("SERVICE_API_KEY", "secret-bank-token")
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert res.status_code == 401
        assert res.json()["error"]["code"] == "unauthorized"


def test_auth_accepts_valid_bearer_token(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("SERVICE_API_KEY", "secret-bank-token")
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
            headers={"Authorization": "Bearer secret-bank-token"},
        )
        assert res.status_code == 200
        assert res.json()["text"] == "Chuyển tiền cho Nam 500k"


def test_auth_accepts_valid_x_api_key_header(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("SERVICE_API_KEY", "secret-bank-token")
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
            headers={"x-api-key": "secret-bank-token"},
        )
        assert res.status_code == 200
        assert res.json()["text"] == "Chuyển tiền cho Nam 500k"


def test_rate_limiting_triggers_429(monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "test-key")
    monkeypatch.setenv("RATE_LIMIT_PER_MINUTE", "2")
    monkeypatch.setattr(transcriptions, "_build_provider", lambda _: _mock_success_provider())
    get_settings.cache_clear()

    with TestClient(app) as client:
        # Request 1
        res1 = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
        )
        assert res1.status_code == 200

        # Request 2
        res2 = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
        )
        assert res2.status_code == 200

        # Request 3 should be blocked
        res3 = client.post(
            "/api/v1/transcriptions",
            files={"audio": ("sample.wav", b"audio-bytes", "audio/wav")},
        )
        assert res3.status_code == 429
        assert res3.json()["error"]["code"] == "rate_limit_exceeded"
        assert "Retry-After" in res3.headers
        assert res3.headers["X-RateLimit-Limit"] == "2"
        assert res3.headers["X-RateLimit-Remaining"] == "0"


def test_cors_preflight_allows_authorization_headers(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,https://pfm.bank.msb.com.vn")
    get_settings.cache_clear()

    with TestClient(app) as client:
        res = client.options(
            "/api/v1/transcriptions",
            headers={
                "Origin": "https://pfm.bank.msb.com.vn",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type, x-api-key",
            },
        )
        assert res.status_code == 200
        assert res.headers["access-control-allow-origin"] == "https://pfm.bank.msb.com.vn"
        assert "POST" in res.headers["access-control-allow-methods"]
