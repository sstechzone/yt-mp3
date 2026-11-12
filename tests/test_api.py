import os
os.environ["API_KEY"] = "test-secret"

from fastapi.testclient import TestClient
from app.main import app, _validate_url
from fastapi import HTTPException

client = TestClient(app)


def test_root():
    response = client.get("/")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_health():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "healthy"


def test_download_requires_api_key():
    response = client.get(
        "/download",
        params={"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert response.status_code == 401


def test_download_rejects_unsupported_host():
    response = client.get(
        "/download",
        params={"url": "https://example.com/video"},
        headers={"X-API-Key": "test-secret"},
    )
    assert response.status_code == 400


def test_validate_youtube_url():
    assert _validate_url("https://youtu.be/dQw4w9WgXcQ").startswith("https://")


def test_validate_rejects_http():
    try:
        _validate_url("http://youtube.com/watch?v=abc")
        assert False, "Expected HTTPException"
    except HTTPException as exc:
        assert exc.status_code == 400
