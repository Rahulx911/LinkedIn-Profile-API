# LinkedIn Profile API

Given a LinkedIn profile URL, returns structured JSON — name, headline,
location, about, experience, education, skills, certifications, languages,
profile images — plus several bonus sections beyond what was asked (projects,
publications, volunteer experience, and more). No browser is used anywhere at
runtime: every call is a plain HTTP request built to look like the ones
LinkedIn's own web app makes.

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
company/location disambiguation needed — on self-view. A fourth profile's
test (see below) found this landmark doesn't exist at all on third-party
profiles (same self-view-only-landmark issue Experience and Education both
had), which show an `"Endorse <Name>"` button per skill instead; both are now
handled. One real gap: LinkedIn paginates skills 10 at a time (`start`/`count`
in the request body), and only the first page is currently fetched — a
profile with more than 10 skills will be missing the rest (see "Known
limitations").

With this, every core field the assignment asked for is populated with real
data.

**Attempt 7 — About and Location, found in two different places.** About
turned out to already be inside the same `above_activity` SDUI response used
for the top card (componentId `profileCardsAboveActivity` covers Analytics,
About, Featured, and Services together) — but not near the literal "About"
heading token in the flattened text stream, which is what an initial hand
inspection assumed. That inspection was looking at a de-duplicated view of the
chunks; in the real (deliberately non-deduped, see `flight.py`) stream the
heading and paragraph aren't adjacent at all. The fix: scan the whole stream
for a single long (>60 char), prose-like string — distinctive enough in
practice since Analytics/Featured/Services in this same response are short
labels or empty, not prose.

Location isn't in any JSON or Flight response at all — it's server-rendered
directly into the profile page's plain HTML, immediately after the top card's
"`<Company> · <School>`" line, with no structured field or wrapping element
identifying it as a location. `_extract_location_from_html()` in `parser.py`
matches that specific adjacency positionally. Both were verified against a
real captured response/page (`tests/fixtures/about_flight_sample.txt`,
`tests/fixtures/profile_html_sample.html`) but only on one profile — a profile
with no current company/school shown in the top card won't have the preceding
line at all, so location would come back `null` in that case (see "Known
limitations").

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

**A third profile surfaced two more noise sources, both now filtered:**
LinkedIn's "you were referred by this job posting" promo banner
(`"LinkedIn helped me get this job"`) and PDF attachment thumbnails
(`"Thumbnail for X.pdf"` / a bare `X.pdf` filename) both appear inline in the
text stream and aren't user-authored content — worse, an attachment's
filename isn't reliably positioned next to the role it belongs to (one
observed case had it attributed to the *following* role instead), so rather
than guess at attribution, both are filtered out entirely. This same test
also surfaced that **role order in the output doesn't always match the
profile page's visual order** — one role appeared first in the underlying
data stream despite being third on the page — even though each role's own
fields (title/company/dates/location/description) are still correctly
grouped together. Given clients would reasonably sort by date anyway, this
wasn't treated as worth chasing further; see "Known limitations."

**A fourth profile validated About/Location and surfaced four more real bugs,
all fixed.** Testing end-to-end against a fourth profile confirmed the About
and Location extraction added later (see below) generalizes beyond the one
profile each was built against, but also caught real regressions no earlier
capture had exercised:

- **About** returned only one paragraph of a five-paragraph About section —
  each paragraph is its own separate text token in the stream, and the
  original "longest single token" pick just grabbed one. Fixed by clustering
  candidate tokens by how close together they sit in the stream (each
  paragraph's tokens land close to each other; unrelated candidates —
  e.g. a "Highlights" mutual-education blurb — sit much farther away) and
  joining the richest cluster.
- **Location** returned a mutual-connections widget's "· 2nd" connection-
  degree badge instead of the real location — that badge happens to match
  the exact same `<p>·</p><div><p>` shape the location regex looks for, and
  sits earlier in the page than the real top card. Fixed by requiring real
  text before the "·" in the first `<p>` (degree badges are bare "· 1st"/
  "· 2nd", nothing before the bullet).
- **Education's school name always came back `null` on third-party
  profiles** — it was sourced from an `"Edit education <School>"` edit-form
  landmark, a self-view-only affordance (same category of self-view
  assumption Experience's title extraction already had to fix). On this
  profile there's no such landmark at all; the school renders as bare text
  instead, in the same position self-view *also* has it (immediately before
  the degree line) — the edit-landmark list was never actually necessary.
  Fixed by reading the school the same way the degree already was: walking
  backward from the date range one extra meaningful token. Also extended the
  date-range pattern to accept a bare `"YYYY – YYYY"` school year (no month),
  found on this profile's secondary-school entry.
- **Experience title/company assignment broke on a profile with *multiple*
  grouped multi-role companies.** The self-view Delhivery capture that
  originally validated multi-role grouping happened to have each sub-role
  show its own employment type inline, which is what let the title-detection
  heuristic recognize an inline title at all. This profile has two grouped
  companies where every sub-role shares one employment type instead — shown
  once in an aggregate line (`"Full-time · 1 yr 8 mos"`) instead of per role
  — so titles fell through to the edit-landmark fallback and, since one role
  has no landmark at all and two share identical landmark text, every
  subsequent title/company/location shifted onto the wrong role. Fixed by
  recognizing a bare title directly followed by its own date range (no
  employment-type marker in between) as a title too, in both the outer
  role-detection loop and the inner description-collection lookahead (which
  otherwise swallowed the next role's title into the current role's
  description). Also extended the duration-badge pattern to match the
  combined `"1 yr 8 mos"` form the aggregate line and one grouped company's
  own duration badge both used, alongside the single-unit `"8 mos"` form an
  earlier profile happened to show.
- **Skills came back completely empty** — the same self-view-only-landmark
  issue as Education's school name, just not yet found there. Skill names
  came only from an `"Edit <Name> skill"` edit-form landmark, which doesn't
  exist on a profile you don't own. Fixed by also recognizing the
  `"Endorse <Name>"` button label third-party profiles show per skill
  instead (you can endorse someone else's skills but not your own, and
  vice versa for editing) — the two are mutually exclusive per profile, so
  checking for either landmark works for both views.

All fixes were re-verified against every existing fixture (no regressions)
plus this profile's real captured data, matching the page exactly across all
7 experience entries, all 3 education entries, and the first page of skills.

**A fifth profile — with multiple co-authored publications — surfaced the
most serious bug found during this project: headline and profile photo can
be attributed to the wrong person entirely.** `headline`/`profile_images`
come from a `MiniProfile` entity recovered opportunistically from subresource
responses (see "Getting the name and headline anyway" above); the code
picked the *first* MiniProfile found anywhere in those responses, silently
assuming there'd only ever be one. This profile's `publications` section
lists each paper's other authors, and each one is its own
`Contributor`→`MiniProfile` entity — so the first one found was a co-author's,
not the profile owner's. The result wasn't a missing field, which would at
least be visible — it was a stranger's occupation (literally the string
`"--"`, that co-author's actual headline placeholder) and, more seriously,
**her profile photo**, returned as if they belonged to the profile being
looked up. Fixed by matching each MiniProfile candidate's own
`publicIdentifier` field against the profile actually being fetched, falling
back to the first one found only when none match — same fix applied to both
`parser.py`'s headline/photo recovery and `client.py`'s Education/Skills
`profile_id` sourcing, which had the identical bug (silently unobserved so
far — LinkedIn's pagination endpoint appears to key off the vanity name
regardless, but this was luck, not a guarantee).

This same profile also showed the About-clustering fix from the previous
round isn't fully safe: the "Highlights" mutual-education blurb sat only 7
tokens before the real About text (vs. 18 on the profile that first
motivated the distance-based clustering), close enough to merge into the
same cluster and prepend unrelated text. Fixed with an explicit exclusion for
that blurb's fixed template (`"You both studied at..."`) rather than relying
on distance alone. `location` came back `null` on this profile too — not a
new bug, just the already-documented case of a top card with only one
identity badge (no company, just a school, so no `"·"` to anchor on).

**A sixth profile prompted stepping back from case-by-case patching toward a
general model.** By this point, About had one more bug: a Featured-item
component embeds several `FeFeaturedItemUrn(...)` internal object reprs —
long (700+ chars), space-containing, and clustered tightly enough to outweigh
the real About paragraph by raw length. Fixed with the same kind of targeted
exclusion as the "Highlights" blurb (checking for the literal `"Urn("`
substring no real prose would ever contain).

Experience was the more significant one. This profile had a role with no
employment-type marker anywhere — neither inline per-role nor a grouped
aggregate line — so it rendered as bare title, then bare company, then
straight to the date range. Every fix up to this point had been a new
special-cased lookahead for whatever specific shape the newest profile
happened to show, and this was the fourth distinct shape in six profiles;
continuing to patch case-by-case wasn't going to converge. Before writing
another special case, the actual resolved JSON was traced directly (not the
flattened text) to check whether there's real tree structure being discarded
that would let this be solved structurally instead of heuristically — there
isn't: a single role's title, company, and date live in entirely separate
top-level chunks with no shared ancestor, rendered through client-component
references ($L<hex>) that are deliberately never resolved (resolving them
was tried earlier in this project and caused severe cross-role duplication,
since the same component definition is reused across many call sites with
per-instance props this decoder doesn't extract). The flattened text stream
really is the only signal available.

Given that, the fix was to generalize rather than special-case again: the
"identity" tokens before each role's date range (title and/or company — the
two fields that can't be recognized by their own shape, only by what follows
them) are now classified by how many unclassifiable tokens sit back-to-back
before the next landmark (an employment type, "Company · Type" line,
duration badge, location, or date) — 0, 1, or 2 — rather than a fixed set of
lookahead shapes. A run of 2 is title-then-company with no type at all; a
run of 1 falls to the existing pair of single-token rules; a run of 0 means
nothing new to classify. This is a strictly larger rule that subsumes every
shape seen across all six profiles under one mechanism (see
`parse_experience_from_flight`'s docstring and `_is_role_landmark`), rather
than a fifth special case bolted onto four existing ones — the intent is
that the *next* new profile's shape has a real chance of already being
covered, rather than guaranteeing another patch.

One real regression surfaced while generalizing: the run-length-2 rule
initially accepted any landmark as confirmation, which misfired against a
stray company-profile URL sitting unclaimed near an already-fixed profile's
data, misreading a real title as a "company" paired with that URL as a fake
"title". Fixed by requiring the confirming token specifically be a date, not
any landmark — a `"Company · Type"` line or bare type there means the second
token isn't a real company at all, just noise ahead of an ordinary
single-token case. Also hardened `_is_noise_token` to exclude bare URLs
generally, matching a precedent `parse_about_from_flight` already had. All
38 tests pass, including every real fixture from all six profiles.

Not fixed by this: this same role's real description text was found (via
direct inspection) sitting at a completely different position in the raw
stream, nowhere near its own date range — LinkedIn's own stream ordering,
not a heuristic gap, and not solvable by a smarter local scan since there's
no reliable anchor connecting the two positions. What gets captured instead
is a stray skill-tag string that happens to sit adjacent. Documented as an
extension of the existing "stream order doesn't always match display order"
limitation.

**A full re-test across all six profiles, plus the first test through the
actual HTTP layer (not just the parser functions directly), caught two more
real issues.** Re-running every profile end-to-end surfaced a location bug
the per-profile testing had missed: `current_location` was reset to `None`
when a new company started via the bare-company-plus-duration rule, but NOT
when a new single-role company started via a combined `"Company · Type"`
line — so a company with no location of its own (confirmed live: Tech
Mahindra, right after a JPMorganChase role that did have one) silently
inherited the previous, unrelated company's location instead of coming back
`null`. Fixed by resetting it in both places.

Testing through a locally running instance of the actual FastAPI app via
curl (rather than calling the parser directly) — the first time this project
validated the real HTTP surface, not just the parsing logic — turned up a
second, smaller gap: a request body missing the required `url` field returns
FastAPI's own built-in validation error shape (`{"detail": [...]}`), which
doesn't match this API's own `{"error": ..., "detail": ...}` contract used
for every other error case. Worth documenting, not worth suppressing
FastAPI's own validation to force a match.

That same curl round, against a real profile, surfaced one more: a bare
country-only location with no city/state and no On-site/Remote/Hybrid suffix
(just the word `"India"`) wasn't recognized by `_is_location_token` at all —
neither existing pattern requires a comma or suffix that a lone country name
doesn't have. Worse, the literal word was leaking into that role's
description as noise instead. Fixed by matching against an explicit country
list (deliberately not a shape-based guess like "any short capitalized
word after a date" — that would risk mistaking a real description's first
word for a location, with no comma or suffix to disambiguate). Fixing this
also incidentally cleaned up the description leak, since the token is now
correctly claimed as `location` instead of falling through as noise.

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
  "location": "India",
  "about": "CS 2025 graduate passionate about building production-grade AI systems...",
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

Error responses from this API's own logic share one shape
(`{"error": "...", "detail": "..."}`):

| Status | Cause |
|---|---|
| 400 | Input isn't a LinkedIn profile URL |
| 401 | Session cookie missing/expired/checkpointed |
| 403 | Profile private / out of network / account restricted |
| 404 | Profile doesn't exist |
| 429 | LinkedIn is rate-limiting this account |
| 502 | Request to LinkedIn timed out/failed at the network level, or LinkedIn returned an unexpected status |

One exception: `422` (malformed request body — e.g. missing the `url`
field) is FastAPI's own built-in request-validation response, shaped
differently (`{"detail": [{"type": ..., "loc": ..., "msg": ..., ...}]}`) —
confirmed via a direct curl against the running app. Not worth overriding
FastAPI's own validation just to force a shape match.

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
  best-effort, verified only against the layouts actually captured across
  six real profiles: Experience's title/company extraction is now a
  generalized rule keyed on how many unclassifiable "identity" tokens sit
  before each role's date range (0/1/2 — see `parse_experience_from_flight`
  and `_is_role_landmark`), tuned against self-view and third-party layouts,
  single- and multi-role companies, grouped multi-role companies with both
  per-role and shared employment types, and a role with no employment type
  at all (career breaks, self-employment, very long histories, or a fifth
  distinct identity-token shape untested — any new one would need extending
  the same generalized rule, not another special case); description
  extraction remains a token-noise heuristic, and — separately from
  title/company — a specific field's text can sit at a stream position
  unconnected to the rest of its own role (confirmed live: a real
  description was found elsewhere in the stream while an unrelated skill-tag
  string got captured in its place), which no local heuristic can catch
  since there's no reliable anchor between the two positions. Education's
  parser handles both self-view and third-party school-name rendering and
  both `"MMM YYYY"` and bare-year date ranges; Skills' parser is the
  simplest of the three (no date/location disambiguation) and handles both
  self-view's "Edit" landmark and third-party's "Endorse" button, tested on
  two profiles.
- **Skills only returns the first page (10).** LinkedIn paginates skills via
  `start`/`count` in the request body; only `start=0` is fetched, so a profile
  with more than 10 skills will be missing the rest. Extending this to loop
  until an empty page would be a small, mechanical follow-up.
- **`/certifications` likely paginates too, and silently returns a partial
  list with no indication more exist.** Confirmed live on a profile with 12
  real certifications where only 6 came back — unlike Skills, this endpoint's
  pagination mechanism (whether it even accepts `start`/`count`, and under
  what parameter names) hasn't been reverse-engineered, so this is
  documented rather than guessed at.
- **Education and Skills additionally depend on a MiniProfile entity being
  available** (same opportunistic source as headline/photo — see below) to
  supply the profile's encoded id, which both their requests require. A
  profile with no content in `projects`/other bonus sections won't surface
  this, in which case both come back empty even if the data exists —
  confirmed live on two of the six profiles tested during development.
- **Experience entries aren't guaranteed to be in page-display order, and
  individual fields can be similarly displaced.** Confirmed live on one
  profile where a whole role appeared first in the parsed output despite
  being third on the actual page (each role's own fields were still
  correctly grouped together there), and on another where just one role's
  description text — not the whole role — sat at a stream position far from
  the rest of that same role's fields, so the wrong (but adjacent) text got
  captured instead.
- **`about` and `location` are still heuristic**, though both are now
  verified against four and three profiles respectively (see "How this was
  reverse engineered"). `about` clusters long prose-like tokens by proximity
  and returns the richest cluster, excluding the two known fixed-shape false
  positives found so far (the "Highlights" mutual-education blurb, and
  Featured-item `FeFeaturedItemUrn(...)` object reprs) — a profile with some
  other unrelated large cluster of prose in the same response (e.g. several
  long Featured post captions close together) could still return the wrong
  text. `location` matches a specific positional
  adjacency in the page HTML (the `<p>` immediately after the top card's
  "`<Company> · <School>`" line, with real text required before the "·") — a
  profile whose top card shows only one identity badge (just a company, or
  just a school, with no "·" joining two) won't match, and location comes
  back `null`; confirmed live on a real profile with only a school badge.
- **`headline` and `profile_images` aren't guaranteed for every profile.**
  Both come from a `MiniProfile` entity that appears as a side effect in some
  subresource responses (e.g. `projects`) — a profile with none of those
  sections populated won't surface it, and both fields will be `null`/empty.
  When more than one MiniProfile is present (e.g. a publication's other
  authors), the one whose `publicIdentifier` matches the profile being
  fetched is preferred — confirmed necessary live on a profile where the
  first one found belonged to a co-author instead, returning her occupation
  and photo. If somehow none match, it falls back to the first one found
  rather than nothing, so a similar mismatch remains possible in principle
  on data shaped differently from anything captured so far.
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
