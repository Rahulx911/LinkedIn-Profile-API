# LinkedIn Profile API

Give it a LinkedIn profile URL, get back structured JSON — name, headline,
location, about, experience, education, skills, certifications, languages,
profile images, plus bonus sections (projects, publications, volunteering, and
more).

**Fully reverse-engineered: no browser at runtime.** Every call is a plain HTTP
request crafted to look like the ones LinkedIn's own web app makes. A browser
(DevTools + HAR capture) was used *once, manually*, to discover the endpoints —
never at request time.

- **Live:** https://linkedin-profile-api-526f.onrender.com
  ([health](https://linkedin-profile-api-526f.onrender.com/healthz) ·
  [API docs](https://linkedin-profile-api-526f.onrender.com/docs))
- **Mirror (Railway):** https://linkedin-profile-api-production-b7c8.up.railway.app
- **Demo video:** https://drive.google.com/file/d/1DWtoIU_vaZPYUYIzbnQgPB-JMyXHJkfv/view?usp=sharing

---

## Try it

```bash
curl -s https://linkedin-profile-api-526f.onrender.com/api/v1/profile \
  -H "Content-Type: application/json" \
  -d '{"url": "https://www.linkedin.com/in/some-person/"}'
```

> First request after ~15 min idle takes 30–50s (Render free-tier cold start) —
> that's the server waking up, not an error.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env      # then fill in LI_AT_COOKIE and JSESSIONID (see that file)
uvicorn app.main:app --reload
```

- Interactive API docs (auto-generated): http://localhost:8000/docs
- Run the pipeline against a real profile: `python scripts/inspect_raw.py <public_identifier>`
- Run tests (synthetic + real captured fixtures, no live calls): `pytest`

Getting the two secrets: log into LinkedIn in your browser → DevTools →
Application → Cookies → `https://www.linkedin.com` → copy `li_at` and
`JSESSIONID`. See `.env.example` for details.

## API

### `POST /api/v1/profile`

Request: `{ "url": "https://www.linkedin.com/in/some-person/" }`

Response `200` (abridged — see `app/models.py` for the full schema):

```json
{
  "public_identifier": "some-person",
  "name": "Jane Doe",
  "headline": "Software Engineer at ExampleCorp",
  "location": "India",
  "about": "CS 2025 graduate passionate about building AI systems...",
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
    "projects": [ { "title": "...", "description": "...", "date_range": {"start": "2022-1", "end": "2022-6"}, "url": null } ]
  }
}
```

Errors share the shape `{"error": "...", "detail": "..."}`:

| Status | Cause |
|---|---|
| 400 | Input isn't a LinkedIn profile URL |
| 401 | Session cookie missing / expired / checkpointed |
| 403 | Profile private / out of network / account restricted |
| 404 | Profile doesn't exist |
| 422 | Malformed request body (e.g. missing `url`) |
| 429 | LinkedIn is rate-limiting this account |
| 502 | Request to LinkedIn failed at the network level, or an unexpected upstream status |

### `GET /healthz`

Liveness check → `{"status": "ok"}`.

## How it works

```
POST /api/v1/profile {"url": "..."}
        │
        ▼  app/main.py — validate URL, check cache
VoyagerClient.fetch_all_raw()  (app/linkedin/client.py)
   1. GET /in/{id}/                          → page HTML (name, location, about)
   2. GET .../certifications, /languages,    → subresource JSON
          /projects, /honors, ... (x10)         (also yields a MiniProfile:
                                                  headline, photo, encoded id)
   3. POST .../actions/component  (Experience)   → React "Flight" wire-format
      POST .../actions/pagination (Education,       streams (decoded by flight.py)
                                   Skills)
        │
        ▼  app/linkedin/parser.py → ProfileResponse schema (app/models.py)
```

**Stack:** FastAPI · httpx · Pydantic · pytest. No database, no ORM — an
in-memory TTL cache keeps repeat lookups of the same profile from re-hitting
LinkedIn.

## Approach (reverse engineering)

The interesting part was that LinkedIn has **deliberately locked down exactly
the highest-value data** while leaving everything else open. Probing the old
per-section REST endpoints gave a clean split:

| Endpoint | Result |
|---|---|
| `/profileView` (combined), `/positions`, `/educations`, `/skills` | `410 Gone` — **retired** |
| `/certifications`, `/languages` | `200` — **alive** (both required) |
| `/projects`, `/publications`, `/honors`, `/courses`, `/patents`, `/organizations`, `/volunteerExperiences`, `/testScores` | `200` — alive (bonus) |

Work history, education, and skills — the three things worth scraping — are the
exact three retired. So those had to be recovered a different way:

- **Name / location / about** — server-rendered directly into the profile page
  HTML (name in `<title>`; location and about via positional/heuristic
  extraction). No API needed.
- **Headline / profile photo** — recovered opportunistically from a
  `MiniProfile` entity LinkedIn embeds as a side effect in some subresource
  responses.
- **Experience / Education / Skills** — LinkedIn's frontend runs on an internal
  **Server-Driven UI** (React Server Components). Found via a HAR capture, these
  render through `rsc-action` endpoints that return React's **"Flight" wire
  format** (not JSON). `app/linkedin/flight.py` implements just enough of that
  format to recover the visible text; `parser.py` heuristically regroups it into
  structured entries. Two distinct action types are involved: `component` (for
  Experience) and `pagination` (for Education/Skills). This is the experimental,
  could-break-anytime part — see limitations.

**Hardened against ~25 real profiles.** The parsers were built and repeatedly
corrected against a large, deliberately varied set of real profiles (self-view
and third-party, 1st/2nd/3rd-degree connections, single/multi-role and grouped
companies, co-authored publications, career breaks, compound employment types,
and varied date/location formats). This caught real bugs a single test profile
never would — e.g. a co-author's photo returned as the profile owner's, a
grouped company's name lost across its sub-roles, description text mistaken for
a location, compound employment types (`Contract Full-time`) breaking the
company/type split, a year-only date with an en dash (`2024 – 2024`) being
dropped, and About returning a Featured post on profiles with no real About.
Two design principles emerged and are worth calling out:

- **Generalize, don't special-case.** The experience title/company logic
  classifies the "identity" tokens before each role's date range by count
  (0/1/2), rather than a growing pile of per-layout lookaheads, so an unseen
  layout has a real chance of already being covered.
- **Anchor on LinkedIn's own structural markers where one exists.** About
  extraction anchors on the expandable "…more" text-block marker that only
  wraps a genuine About paragraph — so profiles with no real About (just
  Featured posts or a Top-skills chip) correctly return `null` instead of
  grabbing unrelated text.

Every fix is locked in by a real-fixture regression test (`pytest`, all green).

<details>
<summary><b>Deployment debugging: two failures behind a generic error</b></summary>

Both cloud deploys initially failed in ways local runs didn't, and both causes
hid behind unhelpful generic responses. Two safe diagnostics (kept in the code)
cracked it: a **startup config check** logging each secret's *length* (never its
value), and **response logging** printing LinkedIn's actual status + body on any
4xx/5xx (never logging secrets).

1. **`999` — truncated cookie.** Startup check showed `LI_AT_COOKIE set (151
   chars)` on the cloud vs `152` locally: one character lost pasting into the
   dashboard. Re-pasting fixed it.
2. **`403 CSRF check failed` — quotes around the token.** This *looked* like
   LinkedIn blocking the datacenter IP (a real, common problem), and reproduced
   on both Render and Railway — so that's the conclusion I first, wrongly,
   reached. The response logging corrected it: the body literally said
   `CSRF check failed`. LinkedIn's `csrf-token` header must equal the *unquoted*
   `JSESSIONID`, but LinkedIn wraps that cookie in quotes and the setup notes had
   said to paste it *with* them. `VoyagerClient` now strips surrounding quotes,
   and **both platforms then returned full data** — proving it was never IP-based.

Lesson: a generic `403` from a cloud host is easy to pattern-match to "IP
blocking," but that's a hypothesis, not a diagnosis — logging the upstream's
actual response body is what turned a plausible-but-wrong guess into the real,
one-line cause.

</details>

<details>
<summary><b>Diagnostic scripts (kept for reference)</b></summary>

- `scripts/inspect_raw.py <id>` — run the full pipeline against a real profile
- `scripts/probe_endpoints.py <id>` — check which per-section endpoints are alive
- `scripts/probe_certifications_pagination.py <id>` — check `/certifications` paging
- `scripts/inspect_html.py <id> "<phrase>"` — check if text is server-rendered in HTML
- `scripts/inspect_sdui.py`, `inspect_education.py`, `inspect_skills.py` — test SDUI actions
- `scripts/list_rsc_actions.py <har>`, `dump_har_entry.py <har> <i>` — inspect a HAR capture
- `scripts/parse_sdui_flight.py <path>`, `debug_tokens.py <path> <token>` — inspect a Flight response

</details>

## Deployment

Deployed to [Render](https://render.com) as a Docker web service, straight from
the `Dockerfile`. To reproduce:

1. Connect this GitHub repo → **New Web Service** (Render auto-detects the
   `Dockerfile`; no build/start overrides needed).
2. Set `LI_AT_COOKIE` and `JSESSIONID` as **secret env vars** (never in the repo).
   `PROFILE_CACHE_TTL_SECONDS` / `LINKEDIN_REQUEST_TIMEOUT_SECONDS` are optional.
3. Deploy. Render injects `PORT`, which the `Dockerfile` reads (falls back to
   `8000` locally). Verify `/healthz`, then a real profile lookup.

The same repo is also mirrored on Railway with the same setup.

## Security

- Secrets live only in env vars (`.env` locally, platform env vars in
  deployment) — never in the repo. `.gitignore` excludes `.env` and
  `debug_output/` (raw captures).
- Credentials and cookies are never logged (the diagnostic logging reports
  secret *lengths* only, and LinkedIn's *responses*, never the secrets themselves).

## Known limitations

**Experimental / could break**
- **Experience, Education, Skills ride on LinkedIn's undocumented internal SDUI
  wire format** — reverse-engineered by hand, not a stable API. Any of them can
  break if LinkedIn changes the format; each fetch is isolated so a failure just
  empties that one section. Field accuracy is best-effort, verified against the
  ~25 real layouts captured (see Approach). Descriptions are a text-position
  heuristic, and occasionally LinkedIn's own stream ordering places a field far
  from the rest of its role — not fixable by a local scan.

**Partial data**
- **Skills** returns only the first page (10); more-than-10 needs a pagination
  loop (small, mechanical follow-up).
- **`/certifications`** returns only ~6 even when the page shows more — confirmed
  *not* a paging bug (the endpoint ignores paging params); the rest come from a
  different, undiscovered source.
- **Education / Skills** need a `MiniProfile` id (same opportunistic source as
  headline/photo); a profile with no bonus-section content won't surface it, and
  those sections come back empty.
- **Experience entries aren't guaranteed to be in page-display order** (sort by
  date if order matters).

**Conditional / heuristic fields**
- **`about` / `location`** are heuristic. `location` (top-card) needs the
  `Company · School` line to anchor on, so a single-badge card (just a school,
  or just a company) → `null`. `about` anchors on the expandable "…more"
  text-block marker that wraps a real About paragraph (so profiles with no
  About — only Featured posts or a Top-skills chip — correctly return `null`);
  a very short About that has no "…more" expansion may still be missed.
- **Experience role order and per-role location fidelity are imperfect.**
  Roles follow LinkedIn's stream order, not a strict reverse-chronological
  sort (sort by `date_range.start` client-side if needed); and a grouped
  company's sub-roles may carry a bare workplace type (`On-site`) as their
  location instead of the city, which sits once on the group header.
- **A bare single-word/city location** with no state, country, or
  On-site/Remote/Hybrid suffix (e.g. a role listing just `"Bangalore"`) isn't
  reliably recognized — a lone capitalized word is ambiguous with the first
  word of a description, so it may be missed or land in the description. Full
  place names (`"Bengaluru, Karnataka, India"`) and bare *country* names are
  handled; individual bare city names deliberately are not, to avoid
  misreading real description text as a location.
- **`headline` / `profile_images`** depend on the `MiniProfile` entity being
  present at all; absent it, they're `null`/empty. When several are present
  (co-authors), the one matching the requested `publicIdentifier` is chosen.
- **Bonus sections** use a generic field mapping; type-specific fields may be
  missed for the rarer sections.

**Operational**
- **Account risk** — automated access violates LinkedIn's ToS and can restrict
  the account. Built/tested against a primary account for this challenge; a
  production system would use a dedicated, disposable one.
- **Session expiry** — `li_at`/`JSESSIONID` expire or hit checkpoints; then the
  API returns `401` and the cookies must be refreshed from the browser.
- **Image URLs are time-limited** (signed with an `e=` expiry).
- **Rate limiting / caching** is in-memory per instance only — no distributed
  limiter.
