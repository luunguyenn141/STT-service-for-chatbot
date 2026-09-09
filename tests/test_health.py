from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app


def test_health_reports_service_status(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    get_settings.cache_clear()
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "provider": "elevenlabs",
        "configured": False,
    }
    get_settings.cache_clear()


def test_root_serves_test_page():
    with TestClient(app) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "Vietnamese STT POC" in response.text
