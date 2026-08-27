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

**Education and Skills remain unsolved.** They almost certainly use the same
mechanism with a different `componentId` — `profileCardsEducationOnly` was a
natural guess following the same naming convention, but it returns `500`
(live-tested), meaning the real id uses some other naming scheme not found in
the time available. Both are always returned as empty lists rather than
guessed at further. Given these two are also exactly among the fields
LinkedIn killed the classic REST endpoints for, this is the same anti-scraping
boundary showing up a third time — see "Known limitations."

## Architecture

```
Client
  │  POST /api/v1/profile {"url": "..."}
  ▼
FastAPI app (app/main.py)
  │  validate URL, check cache
  ▼
VoyagerClient.fetch_all_raw() (app/linkedin/client.py)
  │  1. GET /in/{public_identifier}/            → page HTML (name via <title>)
  │  2. GET .../certifications, /languages,
  │        /projects, /honors, ... (x10)         → subresource JSON
  │  3. POST /flagship-web/rsc-action/.../component  → Experience, as a React
  │        (experimental — see "How this was            Flight-format stream
  │         reverse engineered")
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
- `scripts/parse_sdui_flight.py <path>` / `scripts/debug_tokens.py <path> <token>` — inspect a raw Flight response
- `scripts/test_experience_parser.py <path>` — runs `parse_experience_from_flight()` against a saved response without a live fetch

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
  "education": [],
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

- **Experience relies on an experimental, undocumented protocol.**
  `parse_experience_from_flight()` reverse engineers LinkedIn's internal SDUI/
  React Server Components wire format, reconstructed by hand from one real
  capture — not a documented or stable API. It could break if LinkedIn changes
  this internal format, and `fetch_sdui_component_raw()` is wrapped so that if
  it ever fails, the rest of the response is unaffected (experience just comes
  back empty rather than the whole request failing). Field accuracy is
  best-effort: title is matched positionally against a separate part of the
  component tree (correct across all layouts tested, but not by construction),
  and description extraction is a token-noise heuristic tuned against a
  multi-role-same-company + two single-role-company layout — unusual profile
  structures (career breaks, self-employment, very long histories) haven't
  been tested and may parse incompletely.
- **Education and skills are not returned.** LinkedIn retired their classic
  REST endpoints (`410 Gone`, confirmed live) and the same SDUI mechanism that
  unlocked Experience needs a different, unconfirmed `componentId` for these
  — see "How this was reverse engineered." Always returned as empty lists.
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
