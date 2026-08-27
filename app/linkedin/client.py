"""
Direct-HTTP LinkedIn Voyager client. No browser at runtime — every call here is
a plain HTTP request built to look like the ones LinkedIn's own web app makes.

Two calls are involved, and they were verified with different levels of confidence
(see README "How this was reverse engineered" for the full writeup):

1. resolve_urn() — fetches the public profile page HTML while authenticated, and
   pulls the internal member URN out of the server-rendered hydration JSON with a
   regex. This avoids depending on any specific "identity resolution" endpoint or
   decoration ID, which change often and aren't documented anywhere.

2. fetch_profile() — calls the exact persisted GraphQL query LinkedIn's own
   frontend fires when a profile page loads, captured directly from a live
   Network-tab session:

       GET /voyager/api/graphql
           ?variables=(memberIdentity:<urn>)
           &queryId=voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a

   This one query returns the top card, About, positions, education, skills,
   certifications, and languages in a single normalized (`data` + `included`)
   payload — LinkedIn consolidated what used to be several separate REST
   endpoints into one GraphQL call.
"""

import re

import httpx

from app.linkedin.exceptions import (
    AuthenticationError,
    ProfileNotAccessibleError,
    ProfileNotFoundError,
    RateLimitedError,
)

PROFILE_GRAPHQL_QUERY_ID = "voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a"

# Matches the member URN LinkedIn embeds in the server-rendered hydration JSON
# on a profile page, e.g. "urn:li:fsd_profile:ACoAADXEQd8B..."
URN_PATTERN = re.compile(r'urn:li:fsd_profile:([A-Za-z0-9_-]+)')

BASE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "application/vnd.linkedin.normalized+json+2.1",
    "X-Restli-Protocol-Version": "2.0.0",
    "X-Li-Lang": "en_US",
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
        if response.status_code == 404:
            raise ProfileNotFoundError(f"Profile '{public_identifier}' not found (404).")
        if response.status_code == 429:
            raise RateLimitedError("LinkedIn is rate-limiting this account (429).")
        # LinkedIn sometimes answers with 200 + a login/checkpoint HTML page
        # instead of a real error status when the cookie is stale.
        if "text/html" in response.headers.get("content-type", "") and "graphql" not in str(response.url):
            pass  # expected for resolve_urn(), which deliberately fetches HTML
        response.raise_for_status()

    def resolve_urn(self, public_identifier: str) -> str:
        response = self._client.get(f"/in/{public_identifier}/")
        if response.status_code == 404:
            raise ProfileNotFoundError(f"Profile '{public_identifier}' not found (404).")
        if response.status_code in (401, 403):
            self._check_auth_response(response, public_identifier)

        match = URN_PATTERN.search(response.text)
        if not match:
            if "authwall" in str(response.url) or "/login" in str(response.url):
                raise AuthenticationError(
                    "LinkedIn redirected to a login/authwall page — the li_at "
                    "cookie is missing, expired, or the account hit a checkpoint."
                )
            raise ProfileNotFoundError(
                f"Could not find a member URN in the page for '{public_identifier}'. "
                "Run scripts/inspect_raw.py to see the raw response and adjust "
                "URN_PATTERN if LinkedIn changed its markup."
            )
        return match.group(1)

    def fetch_profile_raw(self, urn: str, public_identifier: str) -> dict:
        url = (
            "/voyager/api/graphql"
            f"?includeWebMetadata=true&variables=(memberIdentity:{urn})"
            f"&queryId={PROFILE_GRAPHQL_QUERY_ID}"
        )
        response = self._client.get(url)
        self._check_auth_response(response, public_identifier)
        return response.json()

    def get_profile_raw(self, public_identifier: str) -> dict:
        urn = self.resolve_urn(public_identifier)
        return self.fetch_profile_raw(urn, public_identifier)
