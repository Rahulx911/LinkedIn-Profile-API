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

import html as html_module
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

# Best-effort — see parse_about_from_flight-style caveats in README. Location
# is server-rendered directly in the page HTML (confirmed live), immediately
# after a "<Company> · <School>" line in the top card. No structured JSON
# field for it was found anywhere in the page, only this positional HTML
# text, so this breaks if that specific adjacency doesn't hold (e.g. a
# profile with no current company/school badges shown).
#
# The first "·"-containing <p> must have real text before the "·" (`[^<]+·`,
# not `[^<]*·`) — found live on a third-party profile where a mutual-
# connections widget's "· 1st"/"· 2nd" degree badge (bullet with NO leading
# text) matches the same <p>·</p><div><p> shape and sits earlier in the page
# than the real top card, so a bare `*` matched that badge instead and
# captured a neighboring badge's text ("· 2nd") as the "location".
LOCATION_PATTERN = re.compile(
    r'<p class="[^"]*">[^<]+·[^<]*</p>\s*<div[^>]*>\s*<p class="[^"]*">([^<]{2,80})</p>'
)

EXCLUDED_TYPE_SUBSTRINGS = ("contributor", "miniprofile", "minicompany", "collectionresponse")


def _all_included(subresources: dict[str, dict | None]) -> list[dict]:
    entities = []
    for raw in subresources.values():
        if raw and isinstance(raw.get("included"), list):
            entities.extend(raw["included"])
    return entities


def _find_mini_profile(
    subresources: dict[str, dict | None], public_identifier: str
) -> dict | None:
    """A subresource can embed MORE THAN ONE MiniProfile — confirmed live on
    a profile with several co-authored publications, where each "Other
    authors" entry is its own Contributor→MiniProfile. Picking just the
    first one found is wrong whenever it isn't the profile owner's: on that
    profile it returned a co-author's occupation and, worse, her profile
    photo, attributed to the wrong person entirely. Every MiniProfile
    carries its own `publicIdentifier`, so prefer the one matching the
    profile actually being fetched; fall back to the first one found only
    when none match (still better than nothing for the headline/photo
    opportunistic-recovery case this was always built around)."""
    candidates = [
        e for e in _all_included(subresources) if "miniprofile" in str(e.get("$type", "")).lower()
    ]
    for entity in candidates:
        if entity.get("publicIdentifier") == public_identifier:
            return entity
    return candidates[0] if candidates else None


def _extract_name_from_title(html: str) -> str | None:
    match = TITLE_PATTERN.search(html)
    if not match:
        return None
    name = match.group(1).strip()
    return name or None


def _extract_location_from_html(html: str) -> str | None:
    match = LOCATION_PATTERN.search(html)
    if not match:
        return None
    location = html_module.unescape(match.group(1)).strip()
    return location or None


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
# Requires each comma-separated part to start capitalized AND be short
# (<= ~38 chars): real place names are short ("Bengaluru", "Karnataka",
# "United States"), but a description sentence that happens to contain one
# comma between two capitalized phrases (confirmed live: "...for Engineering,
# Product and Design teams...") would otherwise match and get mistaken for a
# location. The length cap on each part is what rejects that without
# rejecting any real multi-part place name.
_LOCATION_ADDRESS_RE = re.compile(r"^[A-Z][A-Za-z .'-]{1,38}(, [A-Z][A-Za-z .'-]{1,38}){1,3}$")

# A bare country name with no city/state and no workplace-type suffix at all
# — confirmed live on a role whose only location text was literally "India".
# Neither _LOCATION_SUFFIX_RE nor _LOCATION_ADDRESS_RE can catch this (no
# suffix, no comma), and a generic "any short capitalized word" rule would
# risk mistaking an ordinary description's first word for a location — so
# this matches against an explicit country list instead of a shape-based
# guess. Not the full ISO list — common English names LinkedIn actually
# renders are enough; missing a rare one just means that specific role's
# location stays null, same as today, not a regression.
_COUNTRIES = frozenset(
    {
        "Afghanistan", "Albania", "Algeria", "Andorra", "Angola",
        "Argentina", "Armenia", "Australia", "Austria", "Azerbaijan",
        "Bahamas", "Bahrain", "Bangladesh", "Barbados", "Belarus",
        "Belgium", "Belize", "Benin", "Bhutan", "Bolivia",
        "Bosnia and Herzegovina", "Botswana", "Brazil", "Brunei",
        "Bulgaria", "Burkina Faso", "Burundi", "Cambodia", "Cameroon",
        "Canada", "Chad", "Chile", "China", "Colombia", "Costa Rica",
        "Croatia", "Cuba", "Cyprus", "Czechia", "Czech Republic",
        "Denmark", "Djibouti", "Dominican Republic", "Ecuador", "Egypt",
        "El Salvador", "Estonia", "Eswatini", "Ethiopia", "Fiji",
        "Finland", "France", "Gabon", "Gambia", "Georgia", "Germany",
        "Ghana", "Greece", "Guatemala", "Guinea", "Guyana", "Haiti",
        "Honduras", "Hungary", "Iceland", "India", "Indonesia", "Iran",
        "Iraq", "Ireland", "Israel", "Italy", "Jamaica", "Japan",
        "Jordan", "Kazakhstan", "Kenya", "Kuwait", "Kyrgyzstan", "Laos",
        "Latvia", "Lebanon", "Lesotho", "Liberia", "Libya",
        "Liechtenstein", "Lithuania", "Luxembourg", "Madagascar",
        "Malawi", "Malaysia", "Maldives", "Mali", "Malta", "Mauritania",
        "Mauritius", "Mexico", "Moldova", "Monaco", "Mongolia",
        "Montenegro", "Morocco", "Mozambique", "Myanmar", "Namibia",
        "Nepal", "Netherlands", "New Zealand", "Nicaragua", "Niger",
        "Nigeria", "North Korea", "North Macedonia", "Norway", "Oman",
        "Pakistan", "Panama", "Papua New Guinea", "Paraguay", "Peru",
        "Philippines", "Poland", "Portugal", "Qatar", "Romania",
        "Russia", "Rwanda", "Saudi Arabia", "Senegal", "Serbia",
        "Sierra Leone", "Singapore", "Slovakia", "Slovenia", "Somalia",
        "South Africa", "South Korea", "South Sudan", "Spain",
        "Sri Lanka", "Sudan", "Suriname", "Sweden", "Switzerland",
        "Syria", "Taiwan", "Tajikistan", "Tanzania", "Thailand", "Togo",
        "Trinidad and Tobago", "Tunisia", "Turkey", "Turkmenistan",
        "Uganda", "Ukraine", "United Arab Emirates", "United Kingdom",
        "United States", "Uruguay", "Uzbekistan", "Venezuela", "Vietnam",
        "Yemen", "Zambia", "Zimbabwe",
    }
)


def _is_location_token(token: str) -> bool:
    return bool(
        _LOCATION_SUFFIX_RE.match(token)
        or _LOCATION_ADDRESS_RE.match(token)
        or token in _COUNTRIES
    )


_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_EMPLOYMENT_TYPES = {
    "Full-time", "Part-time", "Internship", "Contract", "Freelance",
    "Self-employed", "Trainee", "Apprenticeship", "Seasonal",
}
_COMPANY_TYPE_RE = re.compile(
    r"^(.+?) · (" + "|".join(re.escape(t) for t in _EMPLOYMENT_TYPES) + r")$"
)
# A grouped multi-role company's aggregate summary line — shown once at the
# top of the group when every sub-role shares the same employment type (see
# _is_bare_company_confirmation docstring below), e.g. "Full-time · 1 yr 8 mos".
_TYPE_DURATION_AGGREGATE_RE = re.compile(
    r"^(" + "|".join(re.escape(t) for t in _EMPLOYMENT_TYPES) + r") · (.+)$"
)
_HASH_CLASS_RE = re.compile(r"^[_a-f0-9]{6,}( [_a-f0-9]{6,})*$")
_NOISE_TOKENS = {"more", "Expanded", "Collapsed", "br", "open"}
_POSITION_ID_RE = re.compile(r"^\d{6,}$")
_ID_LIKE_RE = re.compile(r"^[A-Za-z0-9]{16,}$")
# A cumulative duration badge, e.g. "10 mos", "2 yrs", or the combined
# "1 yr 8 mos" — confirmed live as a grouped company's total across all its
# sub-roles (single-unit forms were the only ones an earlier profile happened
# to show; the combined form showed up on a profile with a longer tenure).
_DURATION_ONLY_RE = re.compile(r"^\d+\s+yrs?(?:\s+\d+\s+mos?)?$|^\d+\s+mos?$")
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
    if token.startswith(
        ("$", "proto.", "com.linkedin.sdui", "/in/", "expandable_text_block_", "http")
    ):
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


def _find_next_meaningful_idx(tokens: list[str], start: int, limit: int = 12) -> int | None:
    for k in range(start, min(start + limit, len(tokens))):
        if not _is_noise_token(tokens[k]):
            return k
    return None


def _is_role_landmark(token: str) -> bool:
    """A token that unambiguously marks a boundary in the role-card grammar
    (see parse_experience_from_flight): an employment type, a combined
    "Company · Type" line, a date range, a duration badge (plain or
    aggregate), or a location. Anything that ISN'T one of these is an
    "identity token" — title or company text — whose role can only be
    determined by what comes after it, not by its own shape."""
    return bool(
        token in _EMPLOYMENT_TYPES
        or _COMPANY_TYPE_RE.match(token)
        or _DATE_RANGE_RE.match(token)
        or _DATE_SINGLE_RE.match(token)
        or _DURATION_ONLY_RE.match(token)
        or _is_location_token(token)
        or _TYPE_DURATION_AGGREGATE_RE.match(token)
    )


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

    The role-card grammar, generalized: this response has no structural
    (tree-based) boundary between roles to exploit — confirmed by tracing
    the actual resolved JSON, where a single role's title, company, and date
    live in entirely separate top-level chunks with no shared ancestor
    (they're rendered through client-component references we deliberately
    don't resolve, since doing so causes cross-role duplication — see
    flight.py). The flattened, ordered text stream is genuinely the only
    signal available, so rather than adding a new special-cased lookahead
    for each new profile's layout permutation (unsustainable — six real
    profiles have already shown four different shapes), the "identity"
    tokens before each date range (title and/or company — the two things
    that can't be recognized by their own shape, only by what follows them)
    are handled as a single generalized rule, keyed on how many
    unclassifiable tokens sit back-to-back before the next `_is_role_landmark`
    (an employment type, "Company · Type" line, duration badge, location, or
    date):
    - 0: nothing to classify — the role reuses sticky title/company state.
    - 1: the single-token rules below decide title vs. company by what
      immediately follows it (type/company-type/date → title;
      duration/aggregate-duration → company).
    - 2: title, then company, with no employment-type marker anywhere for
      that role — confirmed live on a profile where an internship simply
      had no type set. Handled by the dedicated two-part rule below (which
      requires the token after the pair to be a DATE specifically, not any
      landmark — a `_COMPANY_TYPE_RE` or bare-type match there means the
      "second token" isn't really a company at all, just unrelated noise
      ahead of an ordinary single-token title case; using any landmark as
      confirmation caused exactly that misfire against a stray company-
      profile URL in real data).
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
            # Reset location here too (matches the bare-company rule below)
            # — confirmed live on a profile where a new single-role company
            # with no location of its own incorrectly inherited the
            # PREVIOUS, unrelated company's sticky location instead of
            # coming back null. The per-role peek a few lines down still
            # sets it fresh from this role's own location token when one
            # exists, so this reset only matters when there genuinely isn't
            # one.
            current_company, current_type = company_match.group(1), company_match.group(2)
            current_location = None
            i += 1
            continue
        if tok in _EMPLOYMENT_TYPES:
            current_type = tok
            i += 1
            continue
        aggregate = _TYPE_DURATION_AGGREGATE_RE.match(tok)
        if aggregate and _DURATION_ONLY_RE.match(aggregate.group(2)):
            # A grouped multi-role company's shared summary line, e.g.
            # "Full-time · 3 yrs 10 mos" — it states the shared employment
            # type and total tenure but is NOT a role/title of its own.
            # Must be consumed here; otherwise it falls through to the
            # identity-token rules below and gets misread as a pending title,
            # which then cascades the real first title into the company slot
            # for every sub-role (confirmed live on a profile with a
            # 4-sub-role BlackBuck group). Distinct from a "Company · Type"
            # line, which is matched earlier and has the opposite order.
            current_type = aggregate.group(1)
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
                # precede a bare employment type, a combined "Company · Type"
                # line, or its own full date range directly — the latter
                # either because a grouped multi-role company shares one
                # employment type in an aggregate line instead of per role,
                # or because the role has no employment type at all, in
                # which case a second bare token — the company — sits
                # between it and the date; both confirmed live) — stop here
                # without consuming it, so the outer loop picks it up.
                nxt_next_idx = _find_next_meaningful_idx(tokens, j + 1)
                nxt_next = tokens[nxt_next_idx] if nxt_next_idx is not None else None
                if not _is_noise_token(nxt) and nxt_next is not None:
                    if (
                        nxt_next in _EMPLOYMENT_TYPES
                        or _COMPANY_TYPE_RE.match(nxt_next)
                        or _DATE_RANGE_RE.match(nxt_next)
                        or _DATE_SINGLE_RE.match(nxt_next)
                    ):
                        break
                    if not _is_role_landmark(nxt_next):
                        nxt_third = _find_next_meaningful(tokens, nxt_next_idx + 1)
                        if nxt_third is not None and _is_role_landmark(nxt_third):
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
        # specifically one immediately followed by either a duration badge
        # ("8 mos", "1 yr 8 mos") or a "<EmploymentType> · <duration>"
        # aggregate line ("Full-time · 1 yr 8 mos", shown once for a grouped
        # multi-role company when every sub-role shares the same type,
        # confirmed live) — both of which only ever appear right after a
        # company name, never after a title. This narrower confirmation (vs.
        # also accepting employment type/location/date as confirmation) is
        # what avoids misreading an inline title — e.g. on a third-party
        # profile, "Computer vision intern" followed by "Internship" — as a
        # company name.
        next_meaningful = _find_next_meaningful(tokens, i + 1)
        aggregate_match = next_meaningful and _TYPE_DURATION_AGGREGATE_RE.match(next_meaningful)

        # A run of TWO bare, otherwise-unclassifiable tokens back-to-back,
        # immediately before a DATE RANGE specifically (not any landmark —
        # narrower on purpose, see below) — confirmed live on a profile
        # where a role had no employment-type marker at all (neither inline
        # per-role nor a grouped aggregate line): title, then company, then
        # straight to the date range. A single bare token can't be told
        # apart as title vs. company without seeing what follows it (that's
        # what the two single-token rules below already do); this
        # run-length-2 case is the one shape neither of them covers, since
        # both of *its* tokens are themselves unclassifiable until the date
        # after the second one confirms it. First token is always the
        # title, second is the company — handled on the next loop iteration
        # by the dedicated branch below.
        #
        # Deliberately requires the third token to be a DATE, not any
        # landmark: a stray junk token (e.g. a bare company-profile URL,
        # confirmed live sitting unclaimed near a role's other landmarks)
        # immediately before an ordinary single bare title would otherwise
        # look identical to this shape if the check accepted a
        # "Company · Type" line or bare employment type as confirmation too
        # — but those cases are exactly what the single-hop title rule below
        # already handles correctly on its own, with no second identity
        # token involved at all.
        if (
            not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and next_meaningful is not None
            and not _is_role_landmark(next_meaningful)
            and next_meaningful not in edit_landmark_titles
        ):
            second_idx = _find_next_meaningful_idx(tokens, i + 1)
            third = _find_next_meaningful(tokens, second_idx + 1) if second_idx is not None else None
            if third is not None and (_DATE_RANGE_RE.match(third) or _DATE_SINGLE_RE.match(third)):
                pending_title = tok
                i += 1
                continue
        # The second half of the run-length-2 case above: a bare token right
        # after an already-pending title, immediately before a date — the
        # company for that pending title, in a layout with no type marker at
        # all. Checked with its own guard (pending_title already set) so it
        # can't be confused with the run-length-1 "bare company" rule below,
        # which fires when NO title is pending yet.
        if (
            not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and pending_title is not None
            and next_meaningful is not None
            and (_DATE_RANGE_RE.match(next_meaningful) or _DATE_SINGLE_RE.match(next_meaningful))
        ):
            current_company = tok
            current_type = None
            current_location = None
            i += 1
            continue
        # Bare company name (no " · <type>" suffix on the same line) —
        # specifically one immediately followed by either a duration badge
        # ("8 mos", "1 yr 8 mos") or a "<EmploymentType> · <duration>"
        # aggregate line ("Full-time · 1 yr 8 mos", shown once for a grouped
        # multi-role company when every sub-role shares the same type,
        # confirmed live) — both of which only ever appear right after a
        # company name, never after a title. This narrower confirmation (vs.
        # also accepting employment type/location/date as confirmation) is
        # what avoids misreading an inline title — e.g. on a third-party
        # profile, "Computer vision intern" followed by "Internship" — as a
        # company name.
        if (
            not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and next_meaningful is not None
            and (
                _DURATION_ONLY_RE.match(next_meaningful)
                or (aggregate_match and _DURATION_ONLY_RE.match(aggregate_match.group(2)))
            )
        ):
            current_company = tok
            current_type = None
            current_location = None
            pending_title = None
            i += 1
            continue
        # Bare inline title (third-party-profile layout) — a standalone token
        # immediately followed by either a bare employment type, a combined
        # "Company · Type" line (single-role companies show title *before*
        # that combined line), or its own full date range directly (a grouped
        # multi-role company's sub-role when every sub-role shares one
        # employment type — shown once in the group's aggregate line instead
        # of per role, confirmed live) — once it's clear this isn't a company
        # name. Guarded by `pending_title is None` so it can't overwrite a
        # title the run-length-2 rule above already claimed for this same
        # token on a previous iteration (that title's own company — the
        # second token in the run — also reaches this point, and would
        # otherwise be misread as ANOTHER title since it too sits directly
        # before the date).
        if (
            pending_title is None
            and not _is_noise_token(tok)
            and tok not in edit_landmark_titles
            and next_meaningful is not None
            and (
                next_meaningful in _EMPLOYMENT_TYPES
                or _COMPANY_TYPE_RE.match(next_meaningful)
                or _DATE_RANGE_RE.match(next_meaningful)
                or _DATE_SINGLE_RE.match(next_meaningful)
            )
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


_EDU_DATE_RANGE_RE = re.compile(
    r"^((?:[A-Za-z]{3} )?\d{4}) [–-] (Present|(?:[A-Za-z]{3} )?\d{4})$"
)


def parse_education_from_flight(raw_text: str | None) -> list[Education]:
    """Experimental — see README "How this was reverse engineered" and
    "Known limitations". Heuristically reconstructs Education entries from
    LinkedIn's SDUI "Flight" wire format response — same underlying protocol
    as parse_experience_from_flight(), but a much simpler layout: school name,
    then a degree/field line, then a date range using an en dash ("–"),
    immediately adjacent — no location/description clutter to work around.

    School name previously came from "Edit education <School>" text — a
    self-view-only edit-form landmark, clustered together separately from the
    actual entries earlier in the stream (same structural split found in
    Experience's position-id landmarks). That only ever worked by index
    coincidence (Nth landmark assumed to belong to the Nth entry) and broke
    entirely on a third-party profile, which has no edit affordance at all —
    school always came back null. Fixed by reading the school name the same
    place the degree already came from: a bare school-name text token sits
    immediately before the degree line in BOTH self- and third-party views
    (confirmed live on both), so walking backward from the date range two
    meaningful tokens instead of one gets degree and school together,
    correctly scoped to that specific entry.

    Also accepts a bare "YYYY – YYYY" date range (no month) alongside the
    original "MMM YYYY – MMM YYYY" — found live on a third-party profile's
    secondary-school entry. Never raises.
    """
    if not raw_text:
        return []

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return []

    entries: list[dict] = []
    for i, tok in enumerate(tokens):
        date_match = _EDU_DATE_RANGE_RE.match(tok)
        if not date_match:
            continue
        nearby: list[str] = []
        k = i - 1
        steps = 0
        while k >= 0 and steps < 12 and len(nearby) < 2:
            if not _is_noise_token(tokens[k]):
                nearby.append(tokens[k])
            k -= 1
            steps += 1
        degree = nearby[0] if len(nearby) >= 1 and not _EDU_DATE_RANGE_RE.match(nearby[0]) else None
        school = nearby[1] if len(nearby) >= 2 and not _EDU_DATE_RANGE_RE.match(nearby[1]) else None
        entries.append(
            {
                "school": school,
                "degree": degree,
                "start": date_match.group(1),
                "end": None if date_match.group(2) == "Present" else date_match.group(2),
            }
        )

    return [
        Education(
            school=entry["school"],
            degree=entry["degree"],
            field_of_study=None,
            date_range=DateRange(start=entry["start"], end=entry["end"]),
            description=None,
        )
        for entry in entries
    ]


_SKILL_EDIT_PREFIX_RE = re.compile(r"^Edit (.+) skill$")
# Third-party profiles have no "Edit <Name> skill" landmark at all — you
# can't edit someone else's skills — but show an "Endorse <Name>" button per
# skill instead (which self-view doesn't, since you can't endorse yourself).
# Same self-view-only-landmark issue Experience and Education both had.
_SKILL_ENDORSE_PREFIX_RE = re.compile(r"^Endorse (.+)$")


def parse_skills_from_flight(raw_text: str | None) -> list[Skill]:
    """Experimental — see README. Same SDUI "pagination" protocol as
    education. Skill names come reliably from "Edit <Name> skill" edit-form
    landmark text on self-view, or "Endorse <Name>" button text on a
    third-party profile (confirmed live — see `_SKILL_ENDORSE_PREFIX_RE`),
    deduped in order. Note: this only covers one page of results (LinkedIn
    paginates skills 10 at a time; only the first page is fetched — see
    README known limitations). Never raises."""
    if not raw_text:
        return []

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return []

    skills: list[Skill] = []
    seen: set[str] = set()
    for tok in tokens:
        match = _SKILL_EDIT_PREFIX_RE.match(tok) or _SKILL_ENDORSE_PREFIX_RE.match(tok)
        if match:
            name = match.group(1)
            if name not in seen:
                skills.append(Skill(name=name))
                seen.add(name)
    return skills


_ABOUT_CLUSTER_GAP = 15


def parse_about_from_flight(raw_text: str | None) -> str | None:
    """Experimental — see README. Same "component" action type and body as
    experience (component id: profileCardsAboveActivity, which covers
    Analytics/About/Services/Featured — not just About).

    Deliberately does NOT look near the literal "About" heading token — it
    turns out to be nowhere near the actual paragraph in the flattened text
    stream (they only *appeared* adjacent in an earlier hand-inspection that
    was looking at a deduplicated view). Instead scans the whole stream for
    long, prose-like strings.

    A multi-paragraph About renders as several separate text tokens, not
    one — confirmed live on a profile whose About had 5 paragraphs; a plain
    "longest single token" pick returned only one paragraph, silently
    dropping the rest. Fix: cluster candidate tokens by how close together
    they sit in the stream (each About paragraph's tokens land within
    `_ABOUT_CLUSTER_GAP` positions of each other, separated by a handful of
    markup-only tokens; unrelated candidates — e.g. a "Highlights" mutual-
    education blurb, a skills summary line — sit much farther from any other
    candidate) and return the cluster with the most total text, joined in
    order. A single-paragraph About is just a cluster of one, so this
    subsumes the original behavior.

    The "Highlights" blurb itself ("You both studied at X from <date> to
    <date>") isn't always far enough away to land in its own cluster —
    confirmed live on a second profile where it sat only 7 tokens before the
    real About and got merged in, prepending unrelated text. Excluded
    explicitly rather than relying on distance alone, since it's a
    recognizable fixed template (second-person, never how About prose reads)
    distinct from any real About content seen so far.

    A profile with Featured items can also embed several `FeFeaturedItemUrn(
    ...)` tokens — internal binding-key object reprs, not text, but long
    (700+ chars) and space-containing enough to pass the prose check, and
    clustered close enough together to outweigh the real About cluster by
    raw length. Excluded by checking for the literal `"Urn("` substring any
    real prose would never contain.

    Still a heuristic, not a real field, and verified against four profiles
    — see README known limitations. Never raises."""
    if not raw_text:
        return None

    try:
        tokens = extract_text_stream(raw_text)
    except Exception:
        return None

    candidates = [
        (i, tok)
        for i, tok in enumerate(tokens)
        if (
            len(tok) > 60
            and " " in tok
            and not _is_noise_token(tok)
            and not tok.startswith(("http", "com.linkedin", "proto.", "You both "))
            and "Urn(" not in tok
        )
    ]
    if not candidates:
        return None

    clusters: list[list[tuple[int, str]]] = [[candidates[0]]]
    for idx, tok in candidates[1:]:
        if idx - clusters[-1][-1][0] <= _ABOUT_CLUSTER_GAP:
            clusters[-1].append((idx, tok))
        else:
            clusters.append([(idx, tok)])

    best = max(clusters, key=lambda c: sum(len(t) for _, t in c))
    return " ".join(tok for _, tok in best)


def parse_profile(raw: dict, public_identifier: str) -> ProfileResponse:
    html = raw.get("html", "")
    subresources: dict[str, dict | None] = raw.get("subresources", {})

    mini_profile = _find_mini_profile(subresources, public_identifier)

    return ProfileResponse(
        public_identifier=public_identifier,
        name=_extract_name_from_title(html),
        headline=mini_profile.get("occupation") if mini_profile else None,
        location=_extract_location_from_html(html),
        about=parse_about_from_flight(raw.get("about_flight")),
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
