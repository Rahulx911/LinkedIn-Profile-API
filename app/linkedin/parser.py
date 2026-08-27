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

What's NOT available: experience, education, skills. LinkedIn has retired the
endpoints for exactly these three (confirmed 410 Gone on every profile) — see
README "Known limitations". They're always returned as empty lists rather
than guessed at.
"""

import re
from typing import Any

from app.models import (
    BonusItem,
    Certification,
    DateRange,
    Language,
    ProfileImage,
    ProfileResponse,
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
        experience=[],  # endpoint retired by LinkedIn — see README known limitations
        education=[],  # endpoint retired by LinkedIn — see README known limitations
        skills=[],  # endpoint retired by LinkedIn — see README known limitations
        certifications=_parse_certifications(subresources.get("certifications")),
        languages=_parse_languages(subresources.get("languages")),
        profile_images=_image_urls_from_picture(mini_profile.get("picture") if mini_profile else None),
        bonus_sections={
            resource: _parse_bonus_section(subresources.get(resource), resource)
            for resource in subresources
            if resource not in ("certifications", "languages")
        },
    )
