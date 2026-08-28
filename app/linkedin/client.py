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
import json
import os
import re
import sys

import httpx

from app.linkedin.exceptions import (
    AuthenticationError,
    ProfileNotAccessibleError,
    ProfileNotFoundError,
    RateLimitedError,
    UpstreamError,
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
    # Confirmed live — the "above the activity feed" block, which includes
    # the top card (name/headline/location) and About section.
    "above_activity": "com.linkedin.sdui.generated.profile.dsl.impl.profileCardsAboveActivity",
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

_SDUI_SKILLS_PAGINATION_BODY_TEMPLATE = (
    '{{"pagerId":"com.linkedin.sdui.pagers.profile.details.skills",'
    '"clientArguments":{{"$type":"proto.sdui.actions.requests.RequestedArguments","requestedStateKeys":[],'
    '"payload":{{"vanityName":"{v}","profileId":"{p}","start":0,"count":10,'
    '"filter":"ProfileSkillCategory_ALL"}},'
    '"requestMetadata":{{"$type":"proto.sdui.common.RequestMetadata"}},"states":[],'
    '"screenId":"com.linkedin.sdui.flagshipnav.profile.ProfileSkillDetails","knownTemplateIds":[]}},'
    '"paginationRequest":{{"$type":"proto.sdui.actions.requests.PaginationRequest",'
    '"pagerId":"com.linkedin.sdui.pagers.profile.details.skills",'
    '"trigger":{{"$case":"itemDistanceTrigger","itemDistanceTrigger":'
    '{{"$type":"proto.sdui.actions.requests.ItemDistanceTrigger","preloadDistance":3,"preloadLength":250}}}},'
    '"retryCount":2,"requestedArguments":{{"$type":"proto.sdui.actions.requests.RequestedArguments",'
    '"requestedStateKeys":[],"payload":{{"vanityName":"{v}","profileId":"{p}","start":0,"count":10,'
    '"filter":"ProfileSkillCategory_ALL"}},'
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


def _extract_mini_profile_id(subresources: dict, public_identifier: str) -> str | None:
    """Finds the profile's opaque encoded id (the "ACoAA..." form) from a
    MiniProfile entity opportunistically present in some subresource
    responses (same source parser.py uses for headline/photo). Needed as
    input to fetch_sdui_education_raw(), which — unlike the experience
    component action — requires this id, not just the vanity name.

    A subresource can embed more than one MiniProfile — confirmed live on a
    profile with several co-authored publications, each listing its other
    authors as their own Contributor→MiniProfile. The first one found isn't
    necessarily the profile owner's (parser.py's _find_mini_profile had the
    exact same bug, confirmed there via a co-author's occupation/photo
    leaking into the response), so prefer the entity whose own
    publicIdentifier matches the profile actually being fetched; fall back
    to the first one found only when none match."""
    candidates: list[dict] = []
    for raw in subresources.values():
        if not raw or not isinstance(raw.get("included"), list):
            continue
        for entity in raw["included"]:
            if "miniprofile" in str(entity.get("$type", "")).lower():
                candidates.append(entity)

    def _id_from(entity: dict) -> str | None:
        urn = entity.get("entityUrn", "")
        return urn.rsplit(":", 1)[-1] if ":" in urn else None

    for entity in candidates:
        if entity.get("publicIdentifier") == public_identifier:
            return _id_from(entity)
    return _id_from(candidates[0]) if candidates else None


class VoyagerClient:
    def __init__(self, li_at_cookie: str, jsessionid: str | None, timeout: float = 15.0):
        cookies = {"li_at": li_at_cookie.strip()}
        headers = dict(BASE_HEADERS)
        if jsessionid:
            # LinkedIn's JSESSIONID cookie value is wrapped in double quotes
            # (e.g. "ajax:123..."), and the csrf-token header must equal the
            # UNQUOTED token. Confirmed live: sending the quoted form as the
            # csrf-token header gets a 403 with body "CSRF check failed",
            # while the unquoted form works — the earlier assumption that
            # quotes should be kept was wrong. Normalize by stripping any
            # surrounding quotes (and whitespace) so this works whether or
            # not the env var / .env value includes them.
            token = jsessionid.strip().strip('"')
            cookies["JSESSIONID"] = token
            headers["csrf-token"] = token
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

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """Thin wrapper around every outgoing call so a transport-level
        failure (timeout, DNS/connection error — LinkedIn never even sent a
        response) raises our own error type instead of a raw httpx exception
        propagating all the way up to FastAPI's generic 500 handler."""
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise UpstreamError(f"Request to LinkedIn failed ({type(exc).__name__}): {exc}") from exc

    def _check_auth_response(self, response: httpx.Response, public_identifier: str) -> None:
        if response.status_code >= 400:
            # Logged so a deployment's own logs (e.g. Render) show LinkedIn's
            # actual response body on a block/denial — the status code alone
            # (401/403/999) doesn't say whether it's a plain auth rejection,
            # an IP/location-based restriction, or a checkpoint challenge
            # page, and those need different fixes. Never logs request
            # headers or cookies, only what LinkedIn sent back.
            print(
                f"LinkedIn response for '{public_identifier}': "
                f"status={response.status_code} body={response.text[:1000]!r}",
                file=sys.stderr,
            )
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
        if response.status_code >= 400:
            raise UpstreamError(
                f"LinkedIn returned an unexpected {response.status_code} for "
                f"'{public_identifier}'."
            )

    def fetch_profile_html(self, public_identifier: str) -> str:
        response = self._request("GET", f"/in/{public_identifier}/")
        if response.status_code in (401, 403, 404):
            self._check_auth_response(response, public_identifier)
        if "authwall" in str(response.url) or "/login" in str(response.url):
            raise AuthenticationError(
                "LinkedIn redirected to a login/authwall page — the li_at "
                "cookie is missing, expired, or the account hit a checkpoint."
            )
        self._check_auth_response(response, public_identifier)
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
        Real errors (401/403/429) still raise; a non-JSON body on an
        otherwise-successful response raises UpstreamError rather than
        letting a raw JSONDecodeError escape."""
        response = self._request(
            "GET", f"/voyager/api/identity/profiles/{public_identifier}/{resource}"
        )
        if response.status_code in (404, 410):
            return None
        self._check_auth_response(response, public_identifier)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise UpstreamError(
                f"LinkedIn returned a non-JSON body for '{resource}' on '{public_identifier}'."
            ) from exc

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
        response = self._request("POST", url, content=body, headers=headers)
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
        response = self._request("POST", url, content=body, headers=headers)
        self._check_auth_response(response, public_identifier)
        return response.text

    def fetch_sdui_skills_raw(self, public_identifier: str, profile_id: str) -> str:
        """Experimental — see module docstring and README. Same "pagination"
        action type as education, captured live from `/in/{id}/details/skills/`.
        Also requires the profile's opaque encoded id. Returns raw Flight-
        format text."""
        span_id = base64.b64encode(os.urandom(8)).decode()
        url = (
            "/flagship-web/rsc-action/actions/pagination"
            f"?sduiid=com.linkedin.sdui.pagers.profile.details.skills&parentSpanId={span_id}"
        )
        headers = {
            "content-type": "application/json",
            "x-li-rsc-stream": "true",
            "x-li-anchor-page-key": "d_flagship3_profile_view_base_skills_details",
            "origin": "https://www.linkedin.com",
            "referer": f"https://www.linkedin.com/in/{public_identifier}/details/skills/",
        }
        body = _SDUI_SKILLS_PAGINATION_BODY_TEMPLATE.format(v=public_identifier, p=profile_id)
        response = self._request("POST", url, content=body, headers=headers)
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

        try:
            about_flight = self.fetch_sdui_component_raw(public_identifier, "above_activity")
        except Exception:
            about_flight = None

        education_flight = None
        skills_flight = None
        profile_id = _extract_mini_profile_id(subresources, public_identifier)
        if profile_id:
            try:
                education_flight = self.fetch_sdui_education_raw(public_identifier, profile_id)
            except Exception:
                education_flight = None
            try:
                skills_flight = self.fetch_sdui_skills_raw(public_identifier, profile_id)
            except Exception:
                skills_flight = None

        return {
            "html": html,
            "subresources": subresources,
            "experience_flight": experience_flight,
            "education_flight": education_flight,
            "skills_flight": skills_flight,
            "about_flight": about_flight,
        }
