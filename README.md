# LinkedIn Profile API

Given a LinkedIn profile URL, returns structured JSON — name, headline,
experience, education, skills, certifications, languages, profile images —
plus several bonus sections beyond what was asked (projects, publications,
volunteer experience, and more). No browser is used anywhere at runtime: every
call is a plain HTTP request built to look like the ones LinkedIn's own web
app makes.

## How this was reverse engineered

This isn't a quick "found a queryId, done" writeup — the actual investigation
went through several dead ends worth documenting, because the dead ends are
the interesting part.

**Attempt 1 — modern GraphQL query.** Opening a profile page with DevTools'
Network tab open shows one persisted GraphQL call on load:

```
GET /voyager/api/graphql
    ?variables=(memberIdentity:<opaque-encoded-id>)
    &queryId=voyagerIdentityDashProfiles.b5c27c04968c409fc0ed3546575b9b7a
```

Inspecting its own schema (`meta.microSchema` in the response) revealed this
query only ever returns `entityUrn` + `versionTag` — a lightweight existence
check, not the full profile. It also requires LinkedIn's *opaque encoded*
profile ID (the `ACoAA...` form), which for the logged-in user's own profile
is available client-side, but for anyone else's isn't obtainable without
running the extra JS-driven bootstrap calls a real browser makes after page
load — which we deliberately didn't replicate, since that would mean running
a browser.

**Attempt 2 — the classic combined REST endpoint.**
`/voyager/api/identity/profiles/{public_identifier}/profileView` is what most
older "LinkedIn scraping" writeups use — it takes the public identifier
directly, no URN needed. Live-tested: `410 Gone`, on every request regardless
of identifier. LinkedIn has retired it outright.

**Attempt 3 — probing the per-section REST endpoints individually.** The old
combined endpoint used to bundle several per-resource endpoints together.
Probing them one at a time (`scripts/probe_endpoints.py`) against a real
account produced a clean, decisive split:

| Endpoint | Status | |
|---|---|---|
| `/identity/profiles/{id}` (base) | `410 Gone` | retired |
| `/identity/profiles/{id}/positions` | `410 Gone` | retired |
| `/identity/profiles/{id}/educations` | `410 Gone` | retired |
| `/identity/profiles/{id}/skills` | `410 Gone` | retired |
| `/identity/profiles/{id}/certifications` | `200 OK` | **alive** |
| `/identity/profiles/{id}/languages` | `200 OK` | **alive** |
| `/identity/profiles/{id}/projects` | `200 OK` | **alive** (bonus) |
| `/identity/profiles/{id}/publications` | `200 OK` | **alive** (bonus) |
| `/identity/profiles/{id}/volunteerExperiences` | `200 OK` | **alive** (bonus) |
| `/identity/profiles/{id}/honors`, `/courses`, `/testScores`, `/patents`, `/organizations` | `200 OK` | **alive** (bonus, often empty) |

The split isn't random — LinkedIn has deliberately retired exactly the three
highest-value scraping targets (work history, education, skills) while
leaving every secondary section, and even the base profile summary endpoint,
alive. That's a strong signal this was a targeted anti-scraping decision, not
incidental API cleanup.

**Getting the name and headline anyway.** With the base profile endpoint gone,
name comes from the profile page's `<title>` tag — LinkedIn always renders it
as `"{Name} | LinkedIn"`, which is stable regardless of any CSS/component
refactor. Headline and profile photo are recovered opportunistically: several
of the still-alive endpoints (e.g. `projects`, when the profile owner is
attributed as a contributor) embed a `MiniProfile` entity as a side effect of
resolving that attribution, and that entity happens to carry `occupation`
(the headline) and `picture`. This is real, confirmed data — but it's only
present when the profile has content in one of those sections, so it isn't
guaranteed to be there for every profile (see limitations).

**Attempt 4 — cracking Experience via LinkedIn's Server-Driven UI protocol.**
Digging into the authenticated profile page's raw HTML directly (rather than
any API) revealed that LinkedIn's current frontend runs on an internal
Server-Driven UI (SDUI) architecture built on React Server Components — the
initial HTML for Experience/Education is just a lazy-load placeholder
(`componentkey="profile_top_card_experience_lazy_anchor_{id}"`), resolved
into real content only by a further request a real browser fires once the
section scrolls into view.

Finding that request required exporting a full HAR capture (with response
bodies) while scrolling to the Experience section, then grepping it for a
distinctive phrase from the actual profile content rather than eyeballing
requests one by one — most of what's captured during normal browsing is
tracking/telemetry noise (`sensorCollect`, and other obfuscated beacon paths),
not real data calls. That led to:

```
POST /flagship-web/rsc-action/actions/component
     ?componentId=com.linkedin.sdui.generated.profile.dsl.impl.profileCardsExperienceOnly
     &sduiid=com.linkedin.sdui.generated.profile.dsl.impl.profileCardsExperienceOnly
     &parentSpanId=<any base64 string>
```

Two things make this genuinely different from a normal API call, and from
another action captured in the same session (a stateful position-*edit*-form
request keyed by a random per-session UUID, which is **not** replicable
outside a live browser): this one's `componentId`/`sduiid` are fixed strings,
identical for every profile, and the only profile-specific data is the plain
`vanityName`, embedded in the POST body — no random session state required.
That's what makes it constructible from a stateless backend.

The response itself isn't JSON — it's React's "Flight" streaming wire format
(line-delimited `<id>:<payload>` chunks that reference each other via
`"$<id>"` strings, normally deserialized by React itself in a browser).
`app/linkedin/flight.py` implements just enough of it — chunk resolution plus
walking the tree for text under `children` keys — to recover the visible text
of the rendered card in order. `parser.py`'s `parse_experience_from_flight()`
then heuristically regroups that flat text stream into structured entries by
recognizing landmarks (date ranges, `"<location> · On-site/Remote/Hybrid"`
lines, `"<company> · <employment type>"` lines) — verified end-to-end against
a real profile with mixed single- and multi-role-per-company layouts (see
`tests/fixtures/experience_flight_sample.txt`, a real captured response).

**Attempt 5 — Education turned out to be a *different* SDUI action entirely.**
Guessing `profileCardsEducationOnly` (following Experience's naming pattern,
same `component` action) returned `500` — not a real access error, just the
wrong id. The actual mechanism only showed up by capturing a full HAR while
scrolling through Education, Projects, Skills, and Certifications together and
listing every `rsc-action` request in it (`scripts/list_rsc_actions.py`).
That surfaced a *third* distinct action type, used for a profile's full
"details" page rather than the homepage card:

```
POST /flagship-web/rsc-action/actions/pagination
     ?sduiid=com.linkedin.sdui.pagers.profile.details.education
     &parentSpanId=<any base64 string>
```

Unlike the Experience `component` action, this one's POST body requires the
profile's *opaque encoded id* (`ACoAA...`), not just the vanity name. That
sounds like the same dead end hit in Attempt 1 — except this time there's
already a source for it sitting in the pipeline: the same `MiniProfile` entity
recovered opportunistically from bonus subresources (used for headline/photo)
carries this exact id in its `entityUrn`. `_extract_mini_profile_id()` in
`client.py` pulls it out, so Education fetches only run when that entity
happened to be available — same conditionality as headline/photo, not a new
limitation.

Education's Flight response has a much simpler, more regular shape than
Experience's (school → degree/field line → date range, no interleaved
location/description noise to work around), and `parse_education_from_flight()`
reconstructs it cleanly — verified against a real 3-entry response spanning a
university and two schools (`tests/fixtures/education_flight_sample.txt`).

**Attempt 6 — Skills, found the fast way.** Rather than guess again, this
capture explicitly scrolled through Education, Projects, Skills, and
Certifications together in one HAR before searching it — and the Skills
pagination request was already sitting in that same list, using the same
`pagination` action as Education:

```
POST /flagship-web/rsc-action/actions/pagination
     ?sduiid=com.linkedin.sdui.pagers.profile.details.skills
     &parentSpanId=<any base64 string>
```

Its Flight response is the simplest of the three: skill names come reliably
from `"Edit <Name> skill"` edit-form landmark text, no date ranges or
company/location disambiguation needed. One real gap: LinkedIn paginates
skills 10 at a time (`start`/`count` in the request body), and only the first
page is currently fetched — a profile with more than 10 skills will be
missing the rest (see "Known limitations").

With this, every core field the assignment asked for is populated with real
data except `about` and `location` — see limitations below for exactly why.

**Validated against a genuinely different, third-party profile.** Every
capture above came from viewing my own profile — which turns out to matter:
LinkedIn renders an "edit" affordance next to your own positions (you can
edit them), and the experience parser's title extraction originally leaned on
that landmark. Testing against someone else's public profile surfaced real,
third-party-specific layout differences the self-view captures never
exercised:

- No edit affordance exists on someone else's profile, so the title renders
  as plain inline text instead — right before its employment type, or right
  before a combined "Company · Type" line for a single-role company.
- A role active under a month shows a single date with no range at all
  (`"Aug 2026 · 1 mo"`), not the `"<start> - <end>"` format every self-view
  role happened to use.
- Location itself has two more formats beyond the one self-view showed: bare
  workplace type with no city (`"Remote"`, no `"<city> · "` prefix), and a
  bare city/state/country address with no workplace-type suffix at all
  (`"Bengaluru, Karnataka, India"`).

All four were fixed and re-verified — the parser now correctly reconstructs a
different person's mixed single- and multi-role-company experience layout,
title/company/location/dates/description all matching the real page. One
finding this test also confirmed *live*, not just theoretically: Education
and Skills require a `MiniProfile` id sourced opportunistically from bonus
subresources (certifications/projects/etc.), and a profile with none of that
content populated — as this one had — means Education/Skills come back empty
even though the profile visibly has both. Real limitation, now demonstrated
rather than just documented.

## Architecture

```
Client
  │  POST /api/v1/profile {"url": "..."}
  ▼
FastAPI app (app/main.py)
  │  validate URL, check cache
  ▼
VoyagerClient.fetch_all_raw() (app/linkedin/client.py)
  │  1. GET /in/{public_identifier}/                    → page HTML (name via <title>)
  │  2. GET .../certifications, /languages,
  │        /projects, /honors, ... (x10)                 → subresource JSON
  │                                                         (also yields MiniProfile:
  │                                                          headline, photo, encoded id)
  │  3. POST .../actions/component       (Experience)   → React Flight-format streams
  │     POST .../actions/pagination      (Education,       (experimental — see
  │                                        Skills)           "How this was reverse
  │        (Education/Skills need the encoded id            engineered")
  │         from step 2)
  ▼
app/linkedin/flight.py → resolves Flight chunks, extracts visible text
  ▼
parser.py → our own ProfileResponse schema (app/models.py)
```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# then fill in LI_AT_COOKIE and JSESSIONID in .env — see comments in that file
```

Run locally:

```bash
uvicorn app.main:app --reload
```

Interactive API docs (auto-generated by FastAPI): `http://localhost:8000/docs`

Run the full pipeline against a real profile and see the parsed result:

```bash
python scripts/inspect_raw.py <public_identifier>
```

Other diagnostic scripts used during development, kept for reference:
- `scripts/probe_endpoints.py <id>` — checks which per-section endpoints are alive
- `scripts/inspect_html.py <id> "<phrase>"` — checks whether a phrase from the profile is server-rendered into the page HTML
- `scripts/inspect_sdui.py <id> [experience|education|skills]` — tests the experimental SDUI "component" action directly
- `scripts/inspect_education.py <id> <profile_id>` / `scripts/inspect_skills.py <id> <profile_id>` — test the experimental SDUI "pagination" action for each section
- `scripts/list_rsc_actions.py <har-file>` / `scripts/dump_har_entry.py <har-file> <index>` — inspect a HAR capture to find new SDUI actions
- `scripts/parse_sdui_flight.py <path>` / `scripts/debug_tokens.py <path> <token>` — inspect a raw Flight response
- `scripts/test_experience_parser.py <path>` / `scripts/test_education_parser.py <path>` — run the parsers against a saved response without a live fetch

Run tests (these use a synthetic fixture, not live LinkedIn calls):

```bash
pytest
```

## API

### `POST /api/v1/profile`

Request:

```json
{ "url": "https://www.linkedin.com/in/some-person/" }
```

Response `200` (see `app/models.py` for the full schema):

```json
{
  "public_identifier": "some-person",
  "name": "Jane Doe",
  "headline": "Software Engineer at ExampleCorp",
  "location": null,
  "about": null,
  "experience": [
    {
      "title": "Software Developer",
      "company": "ExampleCorp",
      "location": "Pune District, Maharashtra, India · On-site",
      "date_range": { "start": "Jul 2026", "end": null },
      "description": null
    }
  ],
  "education": [
    {
      "school": "Example University",
      "degree": "Bachelor of Technology - BTech, Computer Science",
      "field_of_study": null,
      "date_range": { "start": "Jul 2021", "end": "Sep 2025" },
      "description": null
    }
  ],
  "skills": [ { "name": "PostgreSQL" }, { "name": "Docker" } ],
  "certifications": [
    { "name": "...", "issuer": "...", "issued_date": "2023-1", "credential_id": null, "credential_url": "..." }
  ],
  "languages": [ { "name": "English", "proficiency": "Professional working" } ],
  "profile_images": [ { "url": "https://media.licdn.com/...", "width": 400, "height": 400 } ],
  "bonus_sections": {
    "projects": [ { "title": "...", "description": "...", "date_range": {"start": "2022-1", "end": "2022-6"}, "url": null } ],
    "publications": [ "..." ],
    "volunteerExperiences": [ "..." ]
  }
}
```

Error responses share one shape (`{"error": "...", "detail": "..."}`):

| Status | Cause |
|---|---|
| 400 | Input isn't a LinkedIn profile URL |
| 401 | Session cookie missing/expired/checkpointed |
| 403 | Profile private / out of network / account restricted |
| 404 | Profile doesn't exist |
| 429 | LinkedIn is rate-limiting this account |

### `GET /healthz`

Liveness check for deployment platforms.

## Security

- The LinkedIn session cookie lives only in environment variables (`.env`
  locally, platform env vars in deployment) — never in the repo. `.gitignore`
  excludes `.env` and `debug_output/` (raw captures from the diagnostic scripts).
- No LinkedIn credentials or cookies are ever logged.

## Known limitations

- **Experience, Education, and Skills all rely on an experimental,
  undocumented protocol.** All three parsers reverse engineer LinkedIn's
  internal SDUI/React Server Components wire format, reconstructed by hand
  from real captures — not a documented or stable API, and *two different*
  action types at that (`component` for Experience, `pagination` for
  Education/Skills). Any of them could break if LinkedIn changes this internal
  format; every fetch is wrapped so a failure never takes down the rest of the
  response (that section just comes back empty). Field accuracy is
  best-effort, verified only against the layouts actually captured:
  Experience's title-matching and description extraction are heuristics tuned
  against a multi-role-same-company + two single-role-company layout (career
  breaks, self-employment, very long histories untested); Education's parser
  is simpler and more regular but was only verified against a
  university-plus-two-schools layout; Skills' parser is the most reliable of
  the three (a single reliable text landmark, no date/location disambiguation).
- **Skills only returns the first page (10).** LinkedIn paginates skills via
  `start`/`count` in the request body; only `start=0` is fetched, so a profile
  with more than 10 skills will be missing the rest. Extending this to loop
  until an empty page would be a small, mechanical follow-up.
- **Education and Skills additionally depend on a MiniProfile entity being
  available** (same opportunistic source as headline/photo — see below) to
  supply the profile's encoded id, which both their requests require. A
  profile with no content in `projects`/other bonus sections won't surface
  this, in which case both come back empty even if the data exists.
- **`about` and `location` are not currently extracted.** They may be
  server-rendered into the page HTML like the name is, but that wasn't
  confirmed, so rather than guess at fragile selectors, these fields are
  returned as `null`.
- **`headline` and `profile_images` aren't guaranteed for every profile.**
  Both come from a `MiniProfile` entity that appears as a side effect in some
  subresource responses (e.g. `projects`) — a profile with none of those
  sections populated won't surface it, and both fields will be `null`/empty.
- **Account risk.** LinkedIn's Terms of Service prohibit automated scraping
  and can restrict the account whose cookie is used here. This was built and
  tested against my own primary LinkedIn account, accepting that tradeoff for
  the scope of this challenge. A production system would use a dedicated,
  disposable account instead.
- **Rate limiting.** Requests are cached in-memory per profile
  (`PROFILE_CACHE_TTL_SECONDS`) to reduce repeat hits, but there's no
  cross-instance cache or distributed rate limiter.
- **Session expiry.** `li_at`/`JSESSIONID` cookies expire (or get invalidated
  by a security checkpoint). When that happens the API returns `401` and the
  cookies need to be refreshed manually from the browser.
- **Visibility limits.** Profiles outside the authenticated account's network,
  or with restricted visibility settings, may return partial data or `403`.
- **Profile image URLs are time-limited.** LinkedIn's CDN URLs for photos are
  signed with an expiry (`e=...` query param); returned URLs aren't permanent.
- **Bonus section field mapping is generic, not per-type-verified.** Only
  `projects`' exact field names were directly confirmed live; the other bonus
  sections (`honors`, `courses`, `testScores`, `patents`, `organizations`)
  reuse the same generic extraction and may have gaps for fields specific to
  that type (see `volunteerExperiences` in the example output, where the role
  title wasn't picked up by the generic mapping).

## Tech stack

FastAPI, httpx, Pydantic, pytest — see `requirements.txt`.
