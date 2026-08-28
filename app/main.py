import re

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.cache import TTLCache
from app.config import get_settings
from app.linkedin.client import VoyagerClient
from app.linkedin.exceptions import (
    AuthenticationError,
    InvalidProfileURLError,
    LinkedInClientError,
    ProfileNotAccessibleError,
    ProfileNotFoundError,
    RateLimitedError,
    UpstreamError,
)
from app.linkedin.parser import parse_profile
from app.models import ErrorResponse, ProfileRequest, ProfileResponse

app = FastAPI(
    title="LinkedIn Profile API",
    description="Given a LinkedIn profile URL, returns structured profile data.",
    version="1.0.0",
)

settings = get_settings()
_cache = TTLCache(ttl_seconds=settings.profile_cache_ttl_seconds)

PROFILE_URL_PATTERN = re.compile(
    r"^https?://(www\.)?linkedin\.com/in/([A-Za-z0-9_%-]+)/?"
)

EXCEPTION_STATUS = {
    InvalidProfileURLError: 400,
    ProfileNotFoundError: 404,
    ProfileNotAccessibleError: 403,
    AuthenticationError: 401,
    RateLimitedError: 429,
    # Transport-level failure (timeout, connection error) or an unexpected
    # response from LinkedIn we don't have a specific mapping for — 502 since
    # it's this API's upstream (LinkedIn), not the client's request, at fault.
    UpstreamError: 502,
}


def extract_public_identifier(url: str) -> str:
    match = PROFILE_URL_PATTERN.match(url.strip())
    if not match:
        raise InvalidProfileURLError(
            "Expected a LinkedIn profile URL like https://www.linkedin.com/in/<username>/"
        )
    return match.group(2)


def get_client() -> VoyagerClient:
    return VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}


@app.post(
    "/api/v1/profile",
    response_model=ProfileResponse,
    responses={code: {"model": ErrorResponse} for code in EXCEPTION_STATUS.values()},
)
def get_profile(request: ProfileRequest):
    public_identifier = extract_public_identifier(request.url)

    cached = _cache.get(public_identifier)
    if cached is not None:
        return cached

    client = get_client()
    try:
        raw = client.fetch_all_raw(public_identifier)
    finally:
        client.close()

    profile = parse_profile(raw, public_identifier)
    _cache.set(public_identifier, profile)
    return profile


@app.exception_handler(LinkedInClientError)
def handle_linkedin_error(request, exc: LinkedInClientError):
    status_code = EXCEPTION_STATUS.get(type(exc), 502)
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=type(exc).__name__, detail=str(exc)).model_dump(),
    )


@app.exception_handler(RequestValidationError)
def handle_validation_error(request: Request, exc: RequestValidationError):
    # FastAPI's own built-in handler for this returns {"detail": [...]} —
    # a differently-shaped body than every other error case here. Reformat
    # into the same {"error", "detail"} contract instead of leaving one
    # documented shape inconsistent with the rest (confirmed live via curl
    # against a malformed request body).
    messages = [
        f"{'.'.join(str(p) for p in err['loc'] if p != 'body')}: {err['msg']}"
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=ErrorResponse(error="ValidationError", detail="; ".join(messages)).model_dump(),
    )
