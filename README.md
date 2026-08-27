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

**What's still unsolved: Experience, Education, Skills.** Digging into the
authenticated profile page's raw HTML directly (rather than any API) revealed
that LinkedIn's current frontend runs on an internal Server-Driven UI (SDUI)
architecture — the initial HTML for these three sections is just a lazy-load
placeholder (`componentkey="profile_top_card_experience_lazy_anchor_{id}"`),
resolved into real content only by a further request a real browser fires
once the section scrolls into view. That resolving call's exact query wasn't
captured (would require live DevTools timing during an actual scroll), so
these three fields are returned as empty lists rather than guessed at. Given
these are exactly the three fields LinkedIn also killed the REST endpoints
for, this isn't a coincidence — it's the same anti-scraping boundary showing
up twice.

## Architecture

```
Client
  │  POST /api/v1/profile {"url": "..."}
  ▼
FastAPI app (app/main.py)
  │  validate URL, check cache
  ▼
VoyagerClient.fetch_all_raw() (app/linkedin/client.py)
  │  1. GET /in/{public_identifier}/         → page HTML (name via <title>)
  │  2. GET .../certifications, /languages,
  │        /projects, /honors, ... (x10)      → subresource JSON, in parallel-friendly calls
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
  "experience": [],
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

- **Experience, education, and skills are not returned.** LinkedIn has
  retired the REST endpoints for exactly these three fields (`410 Gone`,
  confirmed live) and gated their replacement behind a client-side lazy-load
  mechanism that wasn't fully reverse engineered in the time available — see
  "How this was reverse engineered" above for the full investigation. They're
  always returned as empty lists.
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
