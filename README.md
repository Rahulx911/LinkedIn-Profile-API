# LinkedIn Profile API

Given a LinkedIn profile URL, returns structured JSON — name, headline, profile
images, certifications, languages, plus several bonus sections beyond what was
asked (projects, publications, volunteer experience, and more). No browser is
used anywhere at runtime: every call is a plain HTTP request built to look like
the ones LinkedIn's own web app makes.

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

**Skills remains unsolved.** Neither the `component` nor the `pagination`
action's id for it was found in the time available — see "Known limitations."
Given skills is also among the fields LinkedIn killed the classic REST
endpoint for, this is the same anti-scraping boundary showing up a third time.

## Architecture

```
Client
  │  POST /api/v1/profile {"url": "..."}
  ▼
FastAPI app (app/main.py)
  │  validate URL, check cache
  ▼
VoyagerClient.fetch_all_raw() (app/linkedin/client.py)
  │  1. GET /in/{public_identifier}/                 → page HTML (name via <title>)
  │  2. GET .../certifications, /languages,
  │        /projects, /honors, ... (x10)              → subresource JSON
  │                                                      (also yields MiniProfile:
  │                                                       headline, photo, encoded id)
  │  3. POST .../actions/component      (Experience)  → React Flight-format streams
  │     POST .../actions/pagination     (Education)      (experimental — see
  │        (needs the encoded id from step 2)              "How this was reverse
  │                                                          engineered")
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
- `scripts/inspect_education.py <id> <profile_id>` — tests the experimental SDUI "pagination" action for Education
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
  "skills": [],
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

- **Experience and Education rely on an experimental, undocumented protocol.**
  Both parsers reverse engineer LinkedIn's internal SDUI/React Server
  Components wire format, reconstructed by hand from real captures — not a
  documented or stable API, and *two different* action types at that
  (`component` vs `pagination`). Either could break if LinkedIn changes this
  internal format; both fetches are wrapped so a failure never takes down the
  rest of the response (that section just comes back empty). Field accuracy
  is best-effort, verified only against the layouts actually captured:
  Experience's title-matching and description extraction are heuristics tuned
  against a multi-role-same-company + two single-role-company layout (career
  breaks, self-employment, very long histories untested); Education's parser
  is simpler and more regular but was only verified against a
  university-plus-two-schools layout.
- **Education additionally depends on a MiniProfile entity being available**
  (same opportunistic source as headline/photo — see below) to supply the
  profile's encoded id, which its request requires. A profile with no
  content in `projects`/other bonus sections won't surface this, in which
  case education comes back empty even if education data exists.
- **Skills is not returned.** LinkedIn retired its classic REST endpoint
  (`410 Gone`, confirmed live), and neither SDUI action's id for it was found
  in the time available — see "How this was reverse engineered." Always
  returned as an empty list.
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
