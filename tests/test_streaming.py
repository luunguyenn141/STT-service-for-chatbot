from __future__ import annotations

import asyncio
import io
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from app.api import streaming
from app.config import Settings, get_settings
from app.main import app
from app.services import streaming as protocol
from app.services.rate_limiter import rate_limiter
from app.services.stt.base import ProviderTranscription, ProviderUnavailable

ORIGIN = "https://chat.example.com"
VOICE = struct.pack("<320h", *([3000, -3000] * 160))
SILENCE = bytes(640)


@pytest.fixture
def client(monkeypatch):
    settings = Settings(_env_file=None, stt_provider="phowhisper", service_api_key="test-secret", phowhisper_preload=False)
    monkeypatch.setattr(streaming, "get_settings", lambda: settings)
    rate_limiter.reset()
    with TestClient(app) as client:
        yield client
    assert streaming._active_sessions == 0


def ticket(client, keyterms=None, endpointing="silence"):
    response = client.post(
        "/api/v1/stream-sessions",
        json={"origin": ORIGIN, "keyterms": keyterms or [], "endpointing": endpointing},
        headers={"x-api-key": "test-secret"},
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    return response.json()["token"]


def connect(client, token):
    return client.websocket_connect("/api/v1/transcriptions/stream", headers={"origin": ORIGIN})


def start(ws, token):
    ws.send_json({"type": "start", "token": token})
    assert ws.receive_json()["type"] == "ready"


def test_ticket_auth_and_origin_validation(client):
    assert client.post("/api/v1/stream-sessions", json={"origin": ORIGIN}).status_code == 401
    assert client.post("/api/v1/stream-sessions", json={"origin": ORIGIN + "/path"}, headers={"x-api-key": "test-secret"}).status_code == 422
    token = ticket(client)
    with client.websocket_connect("/api/v1/transcriptions/stream", headers={"origin": "https://other.example.com"}) as ws:
        ws.send_json({"type": "start", "token": token})
        assert ws.receive_json()["code"] == "unauthorized"


def test_ticket_expiration_tampering_and_shared_secret(monkeypatch):
    settings = Settings(_env_file=None, service_api_key="shared-key")
    monkeypatch.setattr(protocol.time, "time", lambda: 1000)
    token = protocol.issue_ticket(settings, ORIGIN)
    assert protocol.verify_ticket(Settings(_env_file=None, service_api_key="shared-key"), token, ORIGIN)
    assert not protocol.verify_ticket(settings, token + "x", ORIGIN)
    assert not protocol.verify_ticket(settings, {"bad": "type"}, ORIGIN)
    monkeypatch.setattr(protocol.time, "time", lambda: 1060)
    assert not protocol.verify_ticket(settings, token, ORIGIN)


def test_ticket_carries_sanitized_session_keyterms(client):
    token = ticket(client, [" hũ Ăn uống ", "hũ Ăn uống", "hũ Tiết kiệm"])
    claims = protocol.read_ticket(Settings(_env_file=None, service_api_key="test-secret"), token, ORIGIN)
    assert claims is not None
    assert claims["keyterms"] == ["hũ Ăn uống", "hũ Tiết kiệm"]
    assert claims["endpointing"] == "silence"

    response = client.post(
        "/api/v1/stream-sessions",
        json={"origin": ORIGIN, "keyterms": ["x" * 65]},
        headers={"x-api-key": "test-secret"},
    )
    assert response.status_code == 422

    long_terms = [f"hũ {'ấ' * 55}{index}" for index in range(20)]
    bounded_token = ticket(client, long_terms)
    bounded_claims = protocol.read_ticket(
        Settings(_env_file=None, service_api_key="test-secret"), bounded_token, ORIGIN,
    )
    assert bounded_claims is not None
    assert 0 < len(bounded_claims["keyterms"]) < len(long_terms)

    manual_claims = protocol.read_ticket(
        Settings(_env_file=None, service_api_key="test-secret"), ticket(client, endpointing="manual"), ORIGIN,
    )
    assert manual_claims is not None
    assert manual_claims["endpointing"] == "manual"
    assert client.post(
        "/api/v1/stream-sessions",
        json={"origin": ORIGIN, "endpointing": "unknown"},
        headers={"x-api-key": "test-secret"},
    ).status_code == 422


def test_pcm_buffer_ignores_silence_then_keeps_preroll_and_stops_at_pause():
    buffer = protocol.UtteranceBuffer(Settings(_env_file=None))
    buffer.feed(SILENCE * 20)
    assert not buffer.audio
    # Split a PCM frame across messages: VAD must be frame-boundary independent.
    audio = VOICE * 20 + SILENCE * 45
    for offset in range(0, len(audio), 100):
        buffer.feed(audio[offset:offset + 100])
    assert buffer.reason == "silence"
    assert buffer.has_speech
    assert bytes(buffer.audio).startswith(SILENCE * 10 + VOICE)


def test_manual_endpointing_keeps_recording_through_pauses_until_stop():
    buffer = protocol.UtteranceBuffer(Settings(_env_file=None), endpointing="manual")
    buffer.feed(VOICE * 20 + SILENCE * 100)
    assert buffer.has_speech
    assert buffer.reason is None
    buffer.feed(VOICE * 5)
    assert buffer.reason is None
    buffer.finish()
    assert buffer.reason == "stop"


def test_pcm_buffer_limits_long_speech_and_ignores_short_click():
    buffer = protocol.UtteranceBuffer(Settings(_env_file=None, stream_max_audio_seconds=5))
    buffer.feed(VOICE + SILENCE * 45)
    assert buffer.reason is None
    assert not buffer.has_speech
    buffer.feed(VOICE * 260)
    assert buffer.reason == "max_duration"
    assert len(buffer.audio) == 5 * 16000 * 2


def test_stream_returns_partial_before_stop_and_final_contains_tail(client, monkeypatch):
    snapshots = []

    class Provider:
        async def transcribe(self, **kwargs):
            with wave.open(io.BytesIO(kwargs["audio_bytes"]), "rb") as wav:
                assert (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) == (16000, 1, 2)
                snapshots.append(wav.readframes(wav.getnframes()))
            return ProviderTranscription(text=f"Đã nghe {len(snapshots[-1])}")

    monkeypatch.setattr(streaming, "_build_provider", lambda _: Provider())
    token = ticket(client)
    with connect(client, token) as ws:
        start(ws, token)
        for _ in range(10):
            ws.send_bytes(VOICE * 5)
        assert ws.receive_json() == {"type": "partial", "text": "Đã nghe 32000"}
        ws.send_bytes(VOICE * 5 + VOICE[:100])
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "finishing"
        final = ws.receive_json()
        assert final == {
            "type": "final",
            "text": "Đã nghe 35300",
            "raw_text": "Đã nghe 35300",
            "refined": False,
            "refinement_status": "disabled",
            "reason": "stop",
        }
    assert snapshots[-1] == VOICE * 55 + VOICE[:100]


def test_silence_auto_finalizes_and_no_speech_does_not_invoke_model(client, monkeypatch):
    class Provider:
        async def transcribe(self, **_):
            return ProviderTranscription(text="Xin chào")
    monkeypatch.setattr(streaming, "_build_provider", lambda _: Provider())
    token = ticket(client)
    with connect(client, token) as ws:
        start(ws, token)
        for chunk in [VOICE * 15, SILENCE * 20, SILENCE * 20, SILENCE * 5]:
            ws.send_bytes(chunk)
        messages = []
        while not messages or messages[-1]["type"] != "final":
            messages.append(ws.receive_json())
        assert messages[-1] == {
            "type": "final",
            "text": "Xin chào",
            "raw_text": "Xin chào",
            "refined": False,
            "refinement_status": "disabled",
            "reason": "silence",
        }
    class NoCall:
        async def transcribe(self, **_):
            pytest.fail("Silence must not invoke inference")
    monkeypatch.setattr(streaming, "_build_provider", lambda _: NoCall())
    with connect(client, token) as ws:
        start(ws, token)
        ws.send_bytes(SILENCE * 10)
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "finishing"
        assert ws.receive_json()["text"] == ""


@pytest.mark.parametrize("payload", [b"x", bytes(16002), b""], ids=["odd-bytes", "oversized", "empty"])
def test_invalid_audio_rejected(client, payload):
    token = ticket(client)
    with connect(client, token) as ws:
        start(ws, token)
        ws.send_bytes(payload)
        assert ws.receive_json()["code"] == "invalid_audio"


def test_provider_failure_is_reported_and_session_slot_released(client, monkeypatch):
    class Provider:
        async def transcribe(self, **_):
            raise ProviderUnavailable
    monkeypatch.setattr(streaming, "_build_provider", lambda _: Provider())
    token = ticket(client)
    with connect(client, token) as ws:
        start(ws, token)
        ws.send_bytes(VOICE * 15)
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "finishing"
        assert ws.receive_json()["code"] == "provider_error"


def test_stream_busy_and_disconnect_cleanup(client, monkeypatch):
    monkeypatch.setattr(streaming, "get_settings", lambda: Settings(_env_file=None, stt_provider="phowhisper", service_api_key="test-secret", stream_max_sessions=1))
    token = ticket(client)
    with connect(client, token) as first:
        start(first, token)
        with connect(client, token) as second:
            second.send_json({"type": "start", "token": token})
            assert second.receive_json()["code"] == "busy"


def test_final_waits_for_inflight_partial_and_uses_latest_audio(client, monkeypatch):
    class SlowProvider:
        async def transcribe(self, **kwargs):
            await asyncio.sleep(0.05)
            return ProviderTranscription(text=str(len(kwargs["audio_bytes"])))
    monkeypatch.setattr(streaming, "_build_provider", lambda _: SlowProvider())
    token = ticket(client)
    with connect(client, token) as ws:
        start(ws, token)
        for _ in range(15):
            ws.send_bytes(VOICE * 5)
        ws.send_json({"type": "stop"})
        results = []
        while not results or results[-1]["type"] != "final":
            results.append(ws.receive_json())
        assert results[-1]["text"] == str(len(VOICE * 75) + 44)
