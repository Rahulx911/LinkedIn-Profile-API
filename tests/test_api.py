import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import main
from app.linkedin.exceptions import (
    AuthenticationError,
    ProfileNotAccessibleError,
    ProfileNotFoundError,
    RateLimitedError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile_response.json"

client = TestClient(main.app)


class FakeVoyagerClient:
    def __init__(self, raw=None, raise_error=None):
        self._raw = raw
        self._raise_error = raise_error

    def get_profile_raw(self, public_identifier: str) -> dict:
        if self._raise_error:
            raise self._raise_error
        return self._raw

    def close(self) -> None:
        pass


@pytest.fixture(autouse=True)
def clear_cache():
    main._cache._store.clear()
    yield
    main._cache._store.clear()


def test_rejects_non_linkedin_url():
    response = client.post("/api/v1/profile", json={"url": "https://example.com/not-linkedin"})
    assert response.status_code == 400
    assert response.json()["error"] == "InvalidProfileURLError"


def test_returns_parsed_profile_for_valid_url(monkeypatch):
    raw = json.loads(FIXTURE.read_text())
    monkeypatch.setattr(main, "get_client", lambda: FakeVoyagerClient(raw=raw))

    response = client.post(
        "/api/v1/profile", json={"url": "https://www.linkedin.com/in/janedoe/"}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Jane Doe"
    assert body["public_identifier"] == "janedoe"


@pytest.mark.parametrize(
    "exc,expected_status",
    [
        (AuthenticationError("bad cookie"), 401),
        (ProfileNotAccessibleError("private"), 403),
        (ProfileNotFoundError("missing"), 404),
        (RateLimitedError("throttled"), 429),
    ],
)
def test_maps_linkedin_errors_to_http_status(monkeypatch, exc, expected_status):
    monkeypatch.setattr(main, "get_client", lambda: FakeVoyagerClient(raise_error=exc))

    response = client.post(
        "/api/v1/profile", json={"url": "https://www.linkedin.com/in/someone/"}
    )

    assert response.status_code == expected_status


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
