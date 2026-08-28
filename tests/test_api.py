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
    UpstreamError,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile_response.json"

client = TestClient(main.app)


class FakeVoyagerClient:
    def __init__(self, raw=None, raise_error=None):
        self._raw = raw
        self._raise_error = raise_error

    def fetch_all_raw(self, public_identifier: str) -> dict:
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
        (UpstreamError("timed out"), 502),
    ],
)
def test_maps_linkedin_errors_to_http_status(monkeypatch, exc, expected_status):
    monkeypatch.setattr(main, "get_client", lambda: FakeVoyagerClient(raise_error=exc))

    response = client.post(
        "/api/v1/profile", json={"url": "https://www.linkedin.com/in/someone/"}
    )

    assert response.status_code == expected_status


def test_malformed_body_matches_error_response_shape():
    # A missing required field triggers FastAPI's own request-validation
    # handling, not our LinkedInClientError path — confirmed live via curl
    # that its default shape ({"detail": [...]}) doesn't match this API's
    # documented {"error", "detail"} contract. handle_validation_error
    # reformats it to match.
    response = client.post("/api/v1/profile", json={"not_url": "oops"})

    assert response.status_code == 422
    body = response.json()
    assert body["error"] == "ValidationError"
    assert "url" in body["detail"]


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("jsessionid", ['"ajax:1234567890"', "ajax:1234567890", '  "ajax:1234567890"  '])
def test_csrf_token_is_normalized_to_unquoted_form(jsessionid):
    # LinkedIn's Voyager API returns 403 "CSRF check failed" when the
    # csrf-token header carries the surrounding double quotes LinkedIn wraps
    # the JSESSIONID cookie value in (confirmed live during deployment — see
    # README). The client must strip them so it works whether the env var is
    # pasted with quotes, without them, or with stray whitespace.
    from app.linkedin.client import VoyagerClient

    c = VoyagerClient(li_at_cookie="testcookie", jsessionid=jsessionid)
    try:
        assert c._client.headers.get("csrf-token") == "ajax:1234567890"
        assert c._client.cookies.get("JSESSIONID") == "ajax:1234567890"
    finally:
        c.close()
