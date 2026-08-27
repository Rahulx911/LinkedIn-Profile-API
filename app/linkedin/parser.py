"""
Parses the raw normalized Voyager GraphQL response (`{"data": ..., "included": [...]}`)
into our own clean ProfileResponse schema.

IMPORTANT — this file is written defensively because the exact `$type` strings and
field names inside `included` were NOT captured live (fetching the real payload
requires an authenticated call, which has to happen in your own run, not through
an automated agent against your live session — see README).

Run `python scripts/inspect_raw.py <public_identifier>` locally first. It will
print every distinct `$type` present in the response. Compare that list against
the TYPE_HINTS below and adjust the substrings if LinkedIn's actual type names
differ — the matching is substring-based specifically so a small naming mismatch
doesn't require rewriting the whole parser.
"""

from typing import Any

from app.models import (
    Certification,
    DateRange,
    Education,
    Experience,
    Language,
    ProfileImage,
    ProfileResponse,
    Skill,
)

TYPE_HINTS = {
    "profile": "profile",
    "position": "position",
    "education": "education",
    "skill": "skill",
    "certification": "certification",
    "language": "language",
    "picture": "picture",
}


def _entities_of(included: list[dict], hint_key: str) -> list[dict]:
    hint = TYPE_HINTS[hint_key]
    return [
        item
        for item in included
        if hint in str(item.get("$type", "")).lower()
    ]


def _date_range(entity: dict) -> DateRange | None:
    date_range = entity.get("dateRange")
    if not date_range:
        return None

    def _fmt(d: dict | None) -> str | None:
        if not d:
            return None
        parts = [str(d[k]) for k in ("year", "month", "day") if d.get(k)]
        return "-".join(parts) if parts else None

    return DateRange(start=_fmt(date_range.get("start")), end=_fmt(date_range.get("end")))


def parse_profile(raw: dict, public_identifier: str) -> ProfileResponse:
    included: list[dict] = raw.get("included", [])

    profile_entities = _entities_of(included, "profile")
    core = profile_entities[0] if profile_entities else {}

    experience = [
        Experience(
            title=pos.get("title"),
            company=pos.get("companyName") or pos.get("company", {}).get("name")
            if isinstance(pos.get("company"), dict)
            else pos.get("companyName"),
            location=pos.get("locationName") or pos.get("location"),
            date_range=_date_range(pos),
            description=pos.get("description"),
        )
        for pos in _entities_of(included, "position")
    ]

    education = [
        Education(
            school=edu.get("schoolName") or edu.get("school", {}).get("name")
            if isinstance(edu.get("school"), dict)
            else edu.get("schoolName"),
            degree=edu.get("degreeName"),
            field_of_study=edu.get("fieldOfStudy"),
            date_range=_date_range(edu),
            description=edu.get("description"),
        )
        for edu in _entities_of(included, "education")
    ]

    skills = [
        Skill(name=skill.get("name"))
        for skill in _entities_of(included, "skill")
        if skill.get("name")
    ]

    certifications = [
        Certification(
            name=cert.get("name"),
            issuer=cert.get("authority"),
            issued_date=_fmt_single_date(cert.get("date")),
            credential_id=cert.get("licenseNumber"),
            credential_url=cert.get("url"),
        )
        for cert in _entities_of(included, "certification")
    ]

    languages = [
        Language(name=lang.get("name"), proficiency=lang.get("proficiency"))
        for lang in _entities_of(included, "language")
        if lang.get("name")
    ]

    profile_images = [
        ProfileImage(url=url)
        for url in _extract_image_urls(_entities_of(included, "picture"))
    ]

    return ProfileResponse(
        public_identifier=public_identifier,
        name=_full_name(core),
        headline=core.get("headline"),
        location=core.get("geoLocationName") or core.get("locationName"),
        about=core.get("summary"),
        experience=experience,
        education=education,
        skills=skills,
        certifications=certifications,
        languages=languages,
        profile_images=profile_images,
    )


def _full_name(core: dict) -> str | None:
    first = core.get("firstName")
    last = core.get("lastName")
    if first or last:
        return " ".join(p for p in (first, last) if p)
    return core.get("name")


def _fmt_single_date(d: dict[str, Any] | None) -> str | None:
    if not d:
        return None
    parts = [str(d[k]) for k in ("year", "month", "day") if d.get(k)]
    return "-".join(parts) if parts else None


def _extract_image_urls(picture_entities: list[dict]) -> list[str]:
    urls: list[str] = []
    for pic in picture_entities:
        root_url = pic.get("rootUrl")
        artifacts = pic.get("artifacts", [])
        for artifact in artifacts:
            segment = artifact.get("fileIdentifyingUrlPathSegment")
            if root_url and segment:
                urls.append(root_url + segment)
    return urls
