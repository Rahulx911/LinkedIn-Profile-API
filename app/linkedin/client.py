"""
Direct-HTTP LinkedIn Voyager client. No browser at runtime — every call here is
a plain HTTP request built to look like the ones LinkedIn's own web app makes.

See README "How this was reverse engineered" for the full investigation. Short
version, confirmed live against a real account:

- The old combined endpoint (`/voyager/api/identity/profiles/{id}/profileView`)
  and the per-section endpoints for the three highest-value fields —
  `/positions`, `/educations`, `/skills` — all return `410 Gone` for every
  request, regardless of profile. LinkedIn has deliberately retired exactly
  the "core resume" data, while leaving secondary sections alive.
- `/certifications` and `/languages` (both required by the assignment) still
  work, plus several sections beyond what was asked: `/projects`, `/honors`,
  `/publications`, `/courses`, `/testScores`, `/patents`, `/organizations`,
  `/volunteerExperiences`. All take the public identifier directly — no URN
  resolution needed.
- The profile's name is read from the page's `<title>` tag (stable: LinkedIn
  always renders it as "{Name} | LinkedIn"). Headline/photo are recovered
  opportunistically from a `MiniProfile` entity that LinkedIn embeds as a
  side-effect in some subresource responses (e.g. `projects`, when a project
  has the profile owner as a contributor) — see parser.py.
- A separate GraphQL query (`voyagerIdentityDashProfiles`) and the modern
  page's embedded "lazy anchor" placeholders for Experience/Education both
  point at a further SDUI-style resolution mechanism for those three fields
  that wasn't fully cracked — see README known limitations.
"""

import re

import httpx

from app.linkedin.exceptions import (
    AuthenticationError,
    ProfileNotAccessibleError,
    ProfileNotFoundError,
    RateLimitedError,
)

# Confirmed alive via live probing (see scripts/probe_endpoints.py). Two of
# these (certifications, languages) are required by the assignment; the rest
# are bonus sections LinkedIn didn't lock down.
SUBRESOURCES = [
    "certifications",
    "languages",
    "projects",
    "honors",
    "publications",
    "courses",
    "testScores",
    "patents",
    "organizations",
    "volunteerExperiences",
]

# Matches the member URN LinkedIn embeds in the server-rendered hydration JSON
# on a profile page. Confirmed live: modern profile pages embed
# "urn:li:member:<id>" (not "urn:li:fsd_profile:..." as older writeups
# describe) — this pattern tries both, preferring fsd_profile if present.
URN_PATTERN = re.compile(r'urn:li:(?:fsd_profile|member):([A-Za-z0-9_-]+)')

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/vnd.linkedin.normalized+json+2.1",
    "Accept-Language": "en-US,en;q=0.9",
    "X-Restli-Protocol-Version": "2.0.0",
    "X-Li-Lang": "en_US",
    "X-Requested-With": "XMLHttpRequest",
    # A real browser always sends this on Voyager calls; LinkedIn's bot
    # detection appears to check for its presence. The exact values here
    # (timezone etc.) don't need to be accurate for the request to be accepted.
    "X-Li-Track": (
        '{"clientVersion":"1.13.20172","mpVersion":"1.13.20172","osName":"web",'
        '"timezoneOffset":5.5,"timezone":"Asia/Calcutta","deviceFormFactor":"DESKTOP",'
        '"mpName":"voyager-web","displayDensity":2,"displayWidth":2560,"displayHeight":1440}'
    ),
    "Referer": "https://www.linkedin.com/feed/",
}


class VoyagerClient:
    def __init__(self, li_at_cookie: str, jsessionid: str | None, timeout: float = 15.0):
        cookies = {"li_at": li_at_cookie}
        headers = dict(BASE_HEADERS)
        if jsessionid:
            # LinkedIn expects the csrf-token header to equal the JSESSIONID
            # cookie value, quotes included.
            cookies["JSESSIONID"] = jsessionid
            headers["csrf-token"] = jsessionid
        else:
            import sys

            print(
                "WARNING: JSESSIONID not set — LinkedIn's Voyager API generally "
                "requires a csrf-token header equal to it, and requests without "
                "one are commonly rejected with 403. Set JSESSIONID in .env.",
                file=sys.stderr,
            )

        self._client = httpx.Client(
            base_url="https://www.linkedin.com",
            headers=headers,
            cookies=cookies,
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def _check_auth_response(self, response: httpx.Response, public_identifier: str) -> None:
        if response.status_code == 401:
            raise AuthenticationError("LinkedIn rejected the session cookie (401).")
        if response.status_code == 403:
            raise ProfileNotAccessibleError(
                f"LinkedIn denied access to '{public_identifier}' (403) — "
                "private profile, out of network, or the account is restricted."
            )
        if response.status_code in (404, 410):
            raise ProfileNotFoundError(
                f"Profile '{public_identifier}' not found, or endpoint retired "
                f"({response.status_code})."
            )
        if response.status_code == 429:
            raise RateLimitedError("LinkedIn is rate-limiting this account (429).")
        response.raise_for_status()

    def fetch_profile_html(self, public_identifier: str) -> str:
        response = self._client.get(f"/in/{public_identifier}/")
        if response.status_code in (401, 403, 404):
            self._check_auth_response(response, public_identifier)
        if "authwall" in str(response.url) or "/login" in str(response.url):
            raise AuthenticationError(
                "LinkedIn redirected to a login/authwall page — the li_at "
                "cookie is missing, expired, or the account hit a checkpoint."
            )
        response.raise_for_status()
        return response.text

    def resolve_urn(self, public_identifier: str) -> str:
        """Not used in the main extraction flow (see module docstring) — kept
        for the GraphQL path, which currently only works for the logged-in
        account's own profile."""
        html = self.fetch_profile_html(public_identifier)
        match = URN_PATTERN.search(html)
        if not match:
            raise ProfileNotFoundError(
                f"Could not find a member URN in the page for '{public_identifier}'."
            )
        return match.group(1)

    def fetch_subresource_raw(self, public_identifier: str, resource: str) -> dict | None:
        """Returns None if this specific subresource is unavailable (404/410)
        — that's expected for some sections on some profiles, and for
        positions/educations/skills on every profile (see module docstring).
        Real errors (401/403/429) still raise."""
        response = self._client.get(f"/voyager/api/identity/profiles/{public_identifier}/{resource}")
        if response.status_code in (404, 410):
            return None
        self._check_auth_response(response, public_identifier)
        return response.json()

    def fetch_all_raw(self, public_identifier: str) -> dict:
        html = self.fetch_profile_html(public_identifier)
        subresources = {
            resource: self.fetch_subresource_raw(public_identifier, resource)
            for resource in SUBRESOURCES
        }
        return {"html": html, "subresources": subresources}
