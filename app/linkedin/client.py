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
- Experience and Education are recovered via LinkedIn's internal SDUI/React
  Server Components protocol — two *different* action types under
  `/flagship-web/rsc-action/actions/`: `component` for Experience (static
  componentId, vanity name only), `pagination` for Education (needs the
  profile's opaque encoded id, sourced from the same MiniProfile above). Both
  return React "Flight" wire format, decoded by app/linkedin/flight.py and
  heuristically structured by parser.py. Experimental — see README.
- Skills remains unsolved: its REST endpoint is `410 Gone`, and the SDUI
  componentId/pagerId for it wasn't found in the time available.
"""

import base64
import os
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

# Captured live via HAR export while scrolling to the Experience section (see
# README). This "component" action renders one profile card server-side and
# streams back a React Server Components ("Flight") response — not JSON.
# componentId/sduiid are static strings, the same for every profile; only the
# vanity name (embedded both as a plain field and inside each binding-state
# key) varies. Unlike the edit-form action captured in the same session, this
# one uses no random per-session UUID, which is what makes it replicable
# outside a live browser.
#
# The "education" and "skills" componentIds below were guesses following the
# same naming convention as "experience", for this SAME "component" action —
# both wrong. Education's real mechanism turned out to be a completely
# different action type (`pagination`, see fetch_sdui_education_raw and its
# _SDUI_EDUCATION_PAGINATION_BODY_TEMPLATE below) rather than a differently-
# named componentId here. Skills' real mechanism remains unfound. Kept here
# as a documented dead end — see README known limitations.
SDUI_COMPONENT_IDS = {
    "experience": "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsExperienceOnly",
    "education": "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsEducationOnly",  # wrong (500); real mechanism is a different action type, see fetch_sdui_education_raw
    "skills": "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsSkillsOnly",  # unverified guess
}

_SDUI_COMPONENT_BODY_TEMPLATE = (
    '{{"clientArguments":{{"payload":{{"isSelfView":false,"vanityName":"{v}",'
    '"replaceableSectionArgs":{{"vanityName":"{v}","hideCardsForGoldenGate":false,'
    '"shouldSetupReplaceableComponent":true,"isSelfView":false,"isSelfViewResolved":false}},'
    '"profileComponentState":{{"profileId":"{v}",'
    '"shouldRefreshScreenOnReappear":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldRefreshScreen{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldFetchFromCache":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateFetchFromCache{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"loadedSections":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateLoadedProfileSections{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldDisplayTabAnchors":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldDisplayTabAnchors{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldReloadTopCardOnReappear":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldReloadTopCardOnReappear{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"deferredTopCardReloadProfileId":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateDeferredTopCardReloadProfileId{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldDisplayStickyHeader":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldDisplayStickyHeader{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldRefreshLanguageDetailScreen":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldRefreshLanguageDetails{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"lastPerformedActionRef":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateLastPerformedActionRef{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldFocusOnReappear":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldFocusOnReappear{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldFocusFeaturedOnReappear":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateShouldFocusFeaturedOnReappear{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"lastFeaturedActionRef":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateLastFeaturedActionRef{v}ProfileComponentState","namespace":"MemoryNamespace"}}}},'
    '"shouldHideProfileCards":{{"type":"com.linkedin.sdui.components.core.BindingImpl",'
    '"value":{{"key":"ProfileComponentStateProfileHideCards{v}ProfileComponentState","namespace":"MemoryNamespace"}}}}}}}},'
    '"states":[],"requestMetadata":{{"$type":"proto.sdui.common.RequestMetadata"}},'
    '"screenId":"com.linkedin.sdui.flagshipnav.profile.Profile","knownTemplateIds":[]}}}}'
)

_SDUI_EDUCATION_PAGINATION_BODY_TEMPLATE = (
    '{{"pagerId":"com.linkedin.sdui.pagers.profile.details.education",'
    '"clientArguments":{{"$type":"proto.sdui.actions.requests.RequestedArguments","requestedStateKeys":[],'
    '"payload":{{"vanityName":"{v}","profileId":"{p}","start":0,"count":10,'
    '"detailSectionReplaceableComponentRef":"com.linkedin.sdui.profile.card.ref{p}EducationDetailsSection"}},'
    '"requestMetadata":{{"$type":"proto.sdui.common.RequestMetadata"}},"states":[],'
    '"screenId":"com.linkedin.sdui.flagshipnav.profile.ProfileEducationDetails","knownTemplateIds":[]}},'
    '"paginationRequest":{{"$type":"proto.sdui.actions.requests.PaginationRequest",'
    '"pagerId":"com.linkedin.sdui.pagers.profile.details.education",'
    '"trigger":{{"$case":"itemDistanceTrigger","itemDistanceTrigger":'
    '{{"$type":"proto.sdui.actions.requests.ItemDistanceTrigger","preloadDistance":3,"preloadLength":250}}}},'
    '"retryCount":2,"requestedArguments":{{"$type":"proto.sdui.actions.requests.RequestedArguments",'
    '"requestedStateKeys":[],"payload":{{"vanityName":"{v}","profileId":"{p}","start":0,"count":10,'
    '"detailSectionReplaceableComponentRef":"com.linkedin.sdui.profile.card.ref{p}EducationDetailsSection"}},'
    '"requestMetadata":{{"$type":"proto.sdui.common.RequestMetadata"}}}}}}}}'
)

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


def _extract_mini_profile_id(subresources: dict) -> str | None:
    """Finds the profile's opaque encoded id (the "ACoAA..." form) from a
    MiniProfile entity opportunistically present in some subresource
    responses (same source parser.py uses for headline/photo). Needed as
    input to fetch_sdui_education_raw(), which — unlike the experience
    component action — requires this id, not just the vanity name."""
    for raw in subresources.values():
        if not raw or not isinstance(raw.get("included"), list):
            continue
        for entity in raw["included"]:
            if "miniprofile" in str(entity.get("$type", "")).lower():
                urn = entity.get("entityUrn", "")
                if ":" in urn:
                    return urn.rsplit(":", 1)[-1]
    return None


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

    def fetch_sdui_component_raw(self, public_identifier: str, component_key: str) -> str:
        """Experimental — see module docstring and README. Returns the raw
        response text (React Server Components "Flight" wire format, not
        JSON) for one profile card. Only "experience" is confirmed to be the
        right componentId; "education"/"skills" are guesses following the
        same naming pattern and may 404 or return something else entirely."""
        component_id = SDUI_COMPONENT_IDS[component_key]
        span_id = base64.b64encode(os.urandom(8)).decode()
        url = (
            "/flagship-web/rsc-action/actions/component"
            f"?componentId={component_id}&sduiid={component_id}&parentSpanId={span_id}"
        )
        headers = {
            "content-type": "application/json",
            "x-li-rsc-stream": "true",
            "x-li-anchor-page-key": "d_flagship3_profile_view_base",
            "origin": "https://www.linkedin.com",
            "referer": f"https://www.linkedin.com/in/{public_identifier}/",
        }
        body = _SDUI_COMPONENT_BODY_TEMPLATE.format(v=public_identifier)
        response = self._client.post(url, content=body, headers=headers)
        self._check_auth_response(response, public_identifier)
        return response.text

    def fetch_sdui_education_raw(self, public_identifier: str, profile_id: str) -> str:
        """Experimental — see module docstring and README. Education lives
        behind a third, distinct SDUI action type from the "component" one
        used for experience: /actions/pagination, captured live from the
        `/in/{id}/details/education/` full-page view. Unlike the component
        action, this one requires the profile's opaque encoded id (not just
        the vanity name) — sourced from the MiniProfile entity opportunistically
        recovered elsewhere (see parser.py's _find_mini_profile / headline).
        Returns raw Flight-format text, same as fetch_sdui_component_raw."""
        span_id = base64.b64encode(os.urandom(8)).decode()
        url = (
            "/flagship-web/rsc-action/actions/pagination"
            f"?sduiid=com.linkedin.sdui.pagers.profile.details.education&parentSpanId={span_id}"
        )
        headers = {
            "content-type": "application/json",
            "x-li-rsc-stream": "true",
            "x-li-anchor-page-key": "d_flagship3_profile_view_base_education_details",
            "origin": "https://www.linkedin.com",
            "referer": f"https://www.linkedin.com/in/{public_identifier}/details/education/",
        }
        body = _SDUI_EDUCATION_PAGINATION_BODY_TEMPLATE.format(v=public_identifier, p=profile_id)
        response = self._client.post(url, content=body, headers=headers)
        self._check_auth_response(response, public_identifier)
        return response.text

    def fetch_all_raw(self, public_identifier: str) -> dict:
        html = self.fetch_profile_html(public_identifier)
        subresources = {
            resource: self.fetch_subresource_raw(public_identifier, resource)
            for resource in SUBRESOURCES
        }
        try:
            experience_flight = self.fetch_sdui_component_raw(public_identifier, "experience")
        except Exception:
            # Experimental path on top of an unofficial internal protocol —
            # never let it take down the rest of the response. See README.
            experience_flight = None

        education_flight = None
        profile_id = _extract_mini_profile_id(subresources)
        if profile_id:
            try:
                education_flight = self.fetch_sdui_education_raw(public_identifier, profile_id)
            except Exception:
                education_flight = None

        return {
            "html": html,
            "subresources": subresources,
            "experience_flight": experience_flight,
            "education_flight": education_flight,
        }
