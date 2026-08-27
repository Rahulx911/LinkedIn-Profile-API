"""
Direct-HTTP LinkedIn Voyager client. No browser at runtime — every call here is
a plain HTTP request built to look like the ones LinkedIn's own web app makes.

get_profile_raw() tries two paths, in order (see README "How this was reverse
engineered" for the full writeup and what's confirmed vs. not):

1. fetch_profile_legacy_raw() — an older Voyager REST endpoint that takes the
   public identifier directly:

       GET /voyager/api/identity/profiles/{public_identifier}/profileView

   This is the primary path. It sidesteps a real problem with the modern path
   below: the GraphQL query wants LinkedIn's opaque encoded profile ID (the
   "ACoAA..." form), which for the *logged-in user's own profile* is available
   client-side, but for anyone else's profile is only obtainable via extra
   JS-driven bootstrap calls a real browser makes after page load — which we
   deliberately aren't replicating, since that would mean running a browser.

2. resolve_urn() + fetch_profile_raw() — the modern path, used only if the
   legacy endpoint is gone. resolve_urn() fetches the public profile page HTML
   while authenticated and regexes the member URN out of it; fetch_profile_raw()
   then calls the exact persisted GraphQL query LinkedIn's own frontend fires
   on page load, captured directly from a live Network-tab session:

       GET /voyager/api/graphql
           ?variables=(memberIdentity:<urn>)
           &queryId=voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a

   As currently implemented this path only reliably works for the logged-in
   account's own profile, for the reason above — see README known limitations.
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

    def fetch_profile_legacy_raw(self, public_identifier: str) -> dict:
        """Older Voyager REST endpoint that takes the public identifier
        directly — no URN resolution needed. LinkedIn appears to keep this
        alive for backward compatibility (older official/mobile clients).
        Used as the primary path here because it sidesteps the problem that
        the GraphQL query's `memberIdentity` wants an opaque encoded profile
        ID that isn't obtainable from a plain (non-JS-executing) HTML fetch
        for anyone other than the logged-in account itself."""
        response = self._client.get(f"/voyager/api/identity/profiles/{public_identifier}/profileView")
        self._check_auth_response(response, public_identifier)
        return response.json()

    def get_profile_raw(self, public_identifier: str) -> dict:
        try:
            return self.fetch_profile_legacy_raw(public_identifier)
        except ProfileNotFoundError:
            pass  # legacy endpoint may be gone/renamed; fall through to the GraphQL path
        urn = self.resolve_urn(public_identifier)
        return self.fetch_profile_raw(urn, public_identifier)
