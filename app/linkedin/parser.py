"""
Parses the bundle produced by VoyagerClient.fetch_all_raw() — profile page
HTML plus a dict of subresource JSON responses — into our ProfileResponse
schema.

What's built from confirmed-real data (captured live, see README):
- name: from the page's <title> tag ("{Name} | LinkedIn") — always present.
- headline, profile_images: from a `MiniProfile` entity LinkedIn embeds as a
  side-effect in some subresource responses (confirmed via `projects`, when
  the profile owner is attributed as a contributor). Not guaranteed present
  for every profile — e.g. one with zero projects/honors/etc. won't have it,
  in which case these come back None/[].
- certifications, languages: from their dedicated endpoints, both confirmed
  live and required by the assignment.
- bonus_sections: generic best-effort extraction from the other confirmed-
  live endpoints beyond what was asked (projects, honors, publications,
  courses, testScores, patents, organizations, volunteerExperiences). Field
  names are mapped generically (title/name, a subtitle-ish field, description,
  dates, url) since exact per-type shapes weren't all individually verified.

- experience, education: reverse engineered via LinkedIn's internal SDUI/React
  Server Components protocol (see README "How this was reverse engineered").
  Experimental — heuristic parsers on top of an undocumented wire format.

What's NOT available: skills. LinkedIn retired its REST endpoint (confirmed
410 Gone) and the SDUI componentId for it wasn't found in the time available
— see README "Known limitations". Always returned as an empty list.
"""

import re
from typing import Any

from app.linkedin.flight import extract_text_stream
from app.models import (
    BonusItem,
    Certification,
    DateRange,
    Education,
    Experience,
    Language,
    ProfileImage,
    ProfileResponse,
    Skill,
)

TITLE_PATTERN = re.compile(r"<title>(.*?)\s*\|\s*LinkedIn</title>", re.IGNORECASE | re.DOTALL)

EXCLUDED_TYPE_SUBSTRINGS = ("contributor", "miniprofile", "minicompany", "collectionresponse")


def _all_included(subresources: dict[str, dict | None]) -> list[dict]:
    entities = []
    for raw in subresources.values():
        if raw and isinstance(raw.get("included"), list):
            entities.extend(raw["included"])
    return entities


def _find_mini_profile(subresources: dict[str, dict | None]) -> dict | None:
    for entity in _all_included(subresources):
        if "miniprofile" in str(entity.get("$type", "")).lower():
            return entity
    return None


def _extract_name_from_title(html: str) -> str | None:
    match = TITLE_PATTERN.search(html)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _image_urls_from_picture(picture: dict[str, Any] | None) -> list[ProfileImage]:
    if not picture:
        return []
    root_url = picture.get("rootUrl")
    artifacts = picture.get("artifacts", [])
    images = []
    for artifact in artifacts:
        segment = artifact.get("fileIdentifyingUrlPathSegment")
        if root_url and segment:
            images.append(
                ProfileImage(
                    url=root_url + segment,
                    width=artifact.get("width"),
                    height=artifact.get("height"),
                )
            )
    return images


def _fmt_date(d: dict[str, Any] | None) -> str | None:
    if not d:
        return None
    parts = [str(d[k]) for k in ("year", "month", "day") if d.get(k)]
    return "-".join(parts) if parts else None


def _date_range(entity: dict) -> DateRange | None:
    time_period = entity.get("timePeriod")
    if not time_period:
        return None
    return DateRange(
        start=_fmt_date(time_period.get("startDate")),
        end=_fmt_date(time_period.get("endDate")),
    )


def _parse_certifications(raw: dict | None) -> list[Certification]:
    if not raw:
        return []
    return [
        Certification(
            name=entity.get("name"),
            issuer=entity.get("authority"),
            issued_date=_fmt_date((entity.get("timePeriod") or {}).get("startDate")),
            credential_id=entity.get("licenseNumber"),
            credential_url=entity.get("url"),
        )
        for entity in raw.get("included", [])
        if "certification" in str(entity.get("$type", "")).lower()
    ]


def _humanize(value: str | None) -> str | None:
    if not value:
        return None
    return value.replace("_", " ").strip().capitalize()


def _parse_languages(raw: dict | None) -> list[Language]:
    if not raw:
        return []
    return [
        Language(name=entity.get("name"), proficiency=_humanize(entity.get("proficiency")))
        for entity in raw.get("included", [])
        if "language" in str(entity.get("$type", "")).lower() and entity.get("name")
    ]


def _clean_subtitle(value: str | None) -> str | None:
    # Some entities put an unresolved URN reference (e.g. a linked education
    # or company) in a field we otherwise treat as display text. Better to
    # drop it than leak an internal identifier into the API response.
    if value and value.startswith("urn:"):
        return None
    return value


def _parse_bonus_section(raw: dict | None, resource: str) -> list[BonusItem]:
    if not raw:
        return []
    singular = resource.rstrip("s").lower()  # "projects" -> "project", "testScores" -> "testscore"
    items = []
    for entity in raw.get("included", []):
        type_lower = str(entity.get("$type", "")).lower()
        if any(excluded in type_lower for excluded in EXCLUDED_TYPE_SUBSTRINGS):
            continue
        if singular not in type_lower.replace("_", ""):
            continue
        items.append(
            BonusItem(
                title=entity.get("title") or entity.get("name"),
                subtitle=_clean_subtitle(
                    entity.get("occupation")
                    or entity.get("authority")
                    or entity.get("issuer")
                    or entity.get("organizationName")
                ),
                description=entity.get("description"),
                date_range=_date_range(entity),
                url=entity.get("url"),
            )
        )
    return items


_DATE_RANGE_RE = re.compile(
    r"^([A-Z][a-z]{2} \d{4}|\d{4}) - (Present|[A-Z][a-z]{2} \d{4}|\d{4})(?:\s*·\s*.+)?$"
)
# A role active less than a month shows as a single date, no range, e.g.
# "Aug 2026 · 1 mo" — confirmed live on a just-started role.
_DATE_SINGLE_RE = re.compile(r"^([A-Z][a-z]{2} \d{4}) · .+$")
_LOCATION_SUFFIX_RE = re.compile(r"^(?:.+ · )?(On-site|Remote|Hybrid)$")
# Bare "City, State, Country" with no workplace-type suffix at all — a third
# location format confirmed live (a role with no listed workplace type).
# Requires each comma-separated part to start capitalized, which real
# description sentences (lowercase words after the first) don't tend to do.
_LOCATION_ADDRESS_RE = re.compile(r"^[A-Z][A-Za-z .'-]+(, [A-Z][A-Za-z .'-]+){1,3}$")


def _is_location_token(token: str) -> bool:
    return bool(_LOCATION_SUFFIX_RE.match(token) or _LOCATION_ADDRESS_RE.match(token))


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMPLOYMENT_TYPES = {
    "Full-time", "Part-time", "Internship", "Contract", "Freelance",
    "Self-employed", "Trainee", "Apprenticeship", "Seasonal",
}
_COMPANY_TYPE_RE = re.compile(
    r"^(.+?) · (" + "|".join(re.escape(t) for t in _EMPLOYMENT_TYPES) + r")$"
)
_HASH_CLASS_RE = re.compile(r"^[_a-f0-9]{6,}( [_a-f0-9]{6,})*$")
_NOISE_TOKENS = {"more", "Expanded", "Collapsed", "br", "open"}
_POSITION_ID_RE = re.compile(r"^\d{6,}$")
_ID_LIKE_RE = re.compile(r"^[A-Za-z0-9]{16,}$")
_DURATION_ONLY_RE = re.compile(r"^\d+\s+(mos?|yrs?)$")
# UI style/property values (font, alignment, sizing, generic tag names) that
# leak into the text stream alongside real content. Recognized generically —
# a single lowercase-leading word, no spaces — rather than by exact value,
# since enumerating every style token LinkedIn's design system might use
# would be a losing game.
_STYLE_TOKEN_RE = re.compile(r"^[a-z][A-Za-z0-9_-]{0,19}$")
# CSS dimension values that leak through alongside real content, e.g. "10.8rem".
_CSS_DIMENSION_RE = re.compile(r"^\d+(\.\d+)?(rem|px|em|vh|vw|%)$")


def _is_noise_token(token: str) -> bool:
    if token in _NOISE_TOKENS:
        return True
    if _HASH_CLASS_RE.match(token):
        return True
    if token.startswith(("$", "proto.", "com.linkedin.sdui", "/in/", "expandable_text_block_")):
        return True
    if "auto-component-" in token or "auto-binding-" in token:
        return True
    if re.fullmatch(r"\d+", token):
        return True
    if re.fullmatch(r"\d+x", token):
        return True
    if token.startswith("var(--") and token.endswith(")"):
        return True
    if _CSS_DIMENSION_RE.match(token):
        return True
    # LinkedIn's "you were referred by this job posting" promo banner —
    # UI chrome, never user-authored content.
    if "LinkedIn helped me get this job" in token or token == "helped me get this job":
        return True
    # PDF/attachment thumbnail labels ("Thumbnail for X.pdf", or a bare
    # filename) — not description content, and not reliably attributable to
    # the right role even when it is (see README known limitations).
    if token.startswith("Thumbnail for ") or re.fullmatch(r".+\.pdf", token):
        return True
    if _ID_LIKE_RE.match(token):
        return True
    if _STYLE_TOKEN_RE.match(token):
        return True
    return False


def _find_next_meaningful(tokens: list[str], start: int, limit: int = 12) -> str | None:
    for k in range(start, min(start + limit, len(tokens))):
        if not _is_noise_token(tokens[k]):
            return tokens[k]
    return None


def _find_prev_meaningful(tokens: list[str], start: int, limit: int = 12) -> str | None:
    for k in range(start, max(start - limit, -1), -1):
        if not _is_noise_token(tokens[k]):
            return tokens[k]
    return None


def parse_experience_from_flight(raw_text: str | None) -> list[Experience]:
    """Experimental — see README "How this was reverse engineered" and
    "Known limitations". Heuristically reconstructs Experience entries from
    LinkedIn's SDUI "Flight" wire format response (an internal React Server
    Components protocol, not a documented API). This scans the flattened,
    ordered visible-text stream for recognizable landmarks — date ranges,
    "<location> · On-site/Remote/Hybrid" lines, "<company> · <employment
    type>" lines — and groups text around them.

    Known weak points: title comes from one of two different mechanisms
    depending on whether the viewer owns the profile (see below), and
    description extraction is a token-noise heuristic, not a real field.
    Never raises — returns whatever it could parse, empty list on total
    failure, since this is explicitly best-effort on top of an unofficial
    internal protocol.

    Title extraction, two cases:
    - Viewing your own profile: LinkedIn renders an "edit" affordance next to
      each position, and the title sits next to that edit landmark
      (`ProfilePositionEditForm`) rather than inline in the visible card text.
    - Viewing someone else's profile: there's no edit affordance (you can't
      edit their profile), so the title renders directly inline, immediately
      before its employment type — confirmed live on a third-party profile,
      which is also where a same-day/short-tenure role surfaces a date format
      with no range at all ("Aug 2026 · 1 mo", handled by _DATE_SINGLE_RE).
    Both sources are collected; the inline one takes priority per-role since
    it's unambiguous, falling back to the edit-landmark list by position.
    """
    if not raw_text:
        return []

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return []

    # Self-view only: each position id appears twice in the stream — once
    # next to its real title (near a "ProfilePositionEditForm" marker), once
    # again later next to an unrelated "<Title> at <Company>" screen title
    # (for the "skill associations" overlay). Only the first occurrence per
    # id is a title.
    edit_landmark_titles: list[str] = []
    seen_position_ids: set[str] = set()
    for i, tok in enumerate(tokens):
        if _POSITION_ID_RE.match(tok) and tok not in seen_position_ids:
            for j in range(i + 1, min(i + 5, len(tokens))):
                if tokens[j].startswith("ProfilePositionEditForm") or tokens[j].startswith(
                    "com.linkedin.sdui.flagshipnav.profile.ProfilePositionEditForm"
                ):
                    break
                if not _is_noise_token(tokens[j]):
                    edit_landmark_titles.append(tokens[j])
                    seen_position_ids.add(tok)
                    break

    roles: list[dict] = []
    current_company: str | None = None
    current_type: str | None = None
    current_location: str | None = None
    pending_title: str | None = None
    edit_landmark_idx = 0
    i, n = 0, len(tokens)
    while i < n:
        tok = tokens[i]
        if _DURATION_ONLY_RE.match(tok):
            i += 1
            continue
        company_match = _COMPANY_TYPE_RE.match(tok)
        if company_match:
            current_company, current_type = company_match.group(1), company_match.group(2)
            i += 1
            continue
        if tok in _EMPLOYMENT_TYPES:
            current_type = tok
            i += 1
            continue
        if _is_location_token(tok):
            current_location = tok
            i += 1
            continue
        date_match = _DATE_RANGE_RE.match(tok) or _DATE_SINGLE_RE.match(tok)
        if date_match:
            start = date_match.group(1)
            end = date_match.group(2) if date_match.re is _DATE_RANGE_RE else "Present"
            j = i + 1
            # A location shortly after this date range (skipping intervening
            # noise tokens — CSS var refs, single-letter tag markers) belongs
            # to this specific role (single-role company layout). Otherwise
            # fall back to the most recently seen location, which for a
            # multi-role company is shared (it appears once, before any dates).
            peek = j
            while peek < n and _is_noise_token(tokens[peek]) and peek - j < 12:
                peek += 1
            if peek < n and _is_location_token(tokens[peek]):
                current_location = tokens[peek]
                j = peek + 1
            location = current_location
            title = pending_title
            if title is None and edit_landmark_idx < len(edit_landmark_titles):
                title = edit_landmark_titles[edit_landmark_idx]
            edit_landmark_idx += 1
            pending_title = None
            desc_parts = []
            while j < n:
                nxt = tokens[j]
                if (
                    _COMPANY_TYPE_RE.match(nxt)
                    or nxt in _EMPLOYMENT_TYPES
                    or _DATE_RANGE_RE.match(nxt)
                    or _DATE_SINGLE_RE.match(nxt)
                    or nxt.startswith(("com.linkedin.sdui", "proto.sdui", "Show all"))
                    or nxt.endswith(" logo")
                    or _UUID_RE.match(nxt)
                    or "media.licdn.com" in nxt
                    or nxt == "WIDTH_AND_HEIGHT"
                ):
                    break
                if nxt in edit_landmark_titles:
                    # A card's title heading occasionally renders structurally
                    # near an adjacent role's content — skip it rather than
                    # treat it as this role's description or stop collecting.
                    j += 1
                    continue
                # An upcoming bare inline title for the *next* role (it can
                # precede either a bare employment type or a combined
                # "Company · Type" line, depending on layout) — stop here
                # without consuming it, so the outer loop picks it up.
                nxt_next = _find_next_meaningful(tokens, j + 1)
                if (
                    not _is_noise_token(nxt)
                    and nxt_next is not None
                    and (nxt_next in _EMPLOYMENT_TYPES or _COMPANY_TYPE_RE.match(nxt_next))
                ):
                    break
                if not _is_noise_token(nxt) and not _is_location_token(nxt):
                    desc_parts.append(nxt)
                j += 1
            roles.append(
                {
                    "title": title,
                    "company": current_company,
                    "start": start,
                    "end": None if end == "Present" else end,
                    "location": location,
                    "description": "\n".join(desc_parts) if desc_parts else None,
                }
            )
            i = j
            continue
        # Bare company name (no " · <type>" suffix on the same line) —
        # specifically one immediately followed by a duration badge ("8 mos"),
        # which only ever appears right after a company name, never after a
        # title. This narrower confirmation (vs. also accepting employment
        # type/location/date as confirmation) is what avoids misreading an
        # inline title — e.g. on a third-party profile, "Computer vision
        # intern" followed by "Internship" — as a company name.
        next_meaningful = _find_next_meaningful(tokens, i + 1)
        if (
            not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and next_meaningful is not None
            and _DURATION_ONLY_RE.match(next_meaningful)
        ):
            current_company = tok
            current_type = None
            current_location = None
            pending_title = None
            i += 1
            continue
        # Bare inline title (third-party-profile layout) — a standalone token
        # immediately followed by either a bare employment type or a combined
        # "Company · Type" line (single-role companies show title *before*
        # that combined line), once it's clear this isn't a company name.
        if (
            not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and next_meaningful is not None
            and (next_meaningful in _EMPLOYMENT_TYPES or _COMPANY_TYPE_RE.match(next_meaningful))
        ):
            pending_title = tok
            i += 1
            continue
        i += 1

    return [
        Experience(
            title=role["title"],
            company=role["company"],
            location=role["location"],
            date_range=DateRange(start=role["start"], end=role["end"]),
            description=role["description"],
        )
        for role in roles
    ]


_EDU_EDIT_PREFIX_RE = re.compile(r"^Edit education (.+)$")
_EDU_DATE_RANGE_RE = re.compile(r"^([A-Za-z]{3} \d{4}) [–-] (Present|[A-Za-z]{3} \d{4})$")


def parse_education_from_flight(raw_text: str | None) -> list[Education]:
    """Experimental — see README "How this was reverse engineered" and
    "Known limitations". Heuristically reconstructs Education entries from
    LinkedIn's SDUI "Flight" wire format response — same underlying protocol
    as parse_experience_from_flight(), but a much simpler layout: school name
    (reliably sourced from "Edit education <School>" text, the same kind of
    edit-form landmark used for experience titles), then a degree/field line,
    then a date range using an en dash ("–"), immediately adjacent — no
    location/description clutter to work around. Never raises.
    """
    if not raw_text:
        return []

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return []

    schools: list[str] = []
    for tok in tokens:
        match = _EDU_EDIT_PREFIX_RE.match(tok)
        if match:
            schools.append(match.group(1))

    entries: list[dict] = []
    for i, tok in enumerate(tokens):
        date_match = _EDU_DATE_RANGE_RE.match(tok)
        if not date_match:
            continue
        prev = _find_prev_meaningful(tokens, i - 1)
        degree = prev if prev is not None and not _EDU_DATE_RANGE_RE.match(prev) else None
        entries.append(
            {
                "degree": degree,
                "start": date_match.group(1),
                "end": None if date_match.group(2) == "Present" else date_match.group(2),
            }
        )

    education = []
    for idx, entry in enumerate(entries):
        education.append(
            Education(
                school=schools[idx] if idx < len(schools) else None,
                degree=entry["degree"],
                field_of_study=None,
                date_range=DateRange(start=entry["start"], end=entry["end"]),
                description=None,
            )
        )
    return education


_SKILL_EDIT_PREFIX_RE = re.compile(r"^Edit (.+) skill$")


def parse_skills_from_flight(raw_text: str | None) -> list[Skill]:
    """Experimental — see README. Same SDUI "pagination" protocol as
    education. Skill names come reliably from "Edit <Name> skill" edit-form
    landmark text, deduped in order. Note: this only covers one page of
    results (LinkedIn paginates skills 10 at a time; only the first page is
    fetched — see README known limitations). Never raises."""
    if not raw_text:
        return []

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return []

    skills: list[Skill] = []
    seen: set[str] = set()
    for tok in tokens:
        match = _SKILL_EDIT_PREFIX_RE.match(tok)
        if match:
            name = match.group(1)
            if name not in seen:
                skills.append(Skill(name=name))
                seen.add(name)
    return skills


def parse_profile(raw: dict, public_identifier: str) -> ProfileResponse:
    html = raw.get("html", "")
    subresources: dict[str, dict | None] = raw.get("subresources", {})

    mini_profile = _find_mini_profile(subresources)

    return ProfileResponse(
        public_identifier=public_identifier,
        name=_extract_name_from_title(html),
        headline=mini_profile.get("occupation") if mini_profile else None,
        location=None,  # not currently extracted — see README known limitations
        about=None,  # not currently extracted — see README known limitations
        experience=parse_experience_from_flight(raw.get("experience_flight")),
        education=parse_education_from_flight(raw.get("education_flight")),
        skills=parse_skills_from_flight(raw.get("skills_flight")),
        certifications=_parse_certifications(subresources.get("certifications")),
        languages=_parse_languages(subresources.get("languages")),
        profile_images=_image_urls_from_picture(mini_profile.get("picture") if mini_profile else None),
        bonus_sections={
            resource: _parse_bonus_section(subresources.get(resource), resource)
            for resource in subresources
            if resource not in ("certifications", "languages")
        },
    )
