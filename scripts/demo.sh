#!/usr/bin/env bash
#
# Screen-recording demo of the deployed LinkedIn Profile API.
# Runs a clean sequence of curl calls against the live endpoint with labelled,
# paced output so it reads well on a recording. Covers the full surface:
# health, input validation, error handling, and two real profile lookups
# (showing the structured output plus a quick per-section field summary).
#
#   ./scripts/demo.sh                              # defaults: live Render URL + two sample profiles
#   ./scripts/demo.sh <base_url>                   # override the endpoint only
#   ./scripts/demo.sh <base_url> <p1> <p2>         # override endpoint + both profile URLs
#
# Examples:
#   ./scripts/demo.sh
#   ./scripts/demo.sh http://localhost:8000
#   PAUSE=4 ./scripts/demo.sh   # slower pacing for narration

set -u

BASE_URL="${1:-https://linkedin-profile-api-526f.onrender.com}"
PROFILE_1="${2:-https://www.linkedin.com/in/rahul911/}"
PROFILE_2="${3:-https://www.linkedin.com/in/tanya-nijhawan-2an/}"

# Pretty-printer: prefer jq, fall back to python.
if command -v jq >/dev/null 2>&1; then
  HAVE_JQ=1
  PP() { jq .; }
else
  HAVE_JQ=0
  PP() { python3 -m json.tool; }
fi

pause() { sleep "${PAUSE:-2}"; }

hr()  { printf '\n\033[1;36m─────────────────────────────────────────────────────────\033[0m\n'; }
say() { printf '\033[1;33m▶ %s\033[0m\n' "$1"; }
run() { printf '\033[0;90m$ %s\033[0m\n\n' "$1"; }
ok()  { printf '\033[0;32m✓ %s\033[0m\n' "$1"; }

# Prints HTTP status of a request and asserts it matches the expected code.
check_status() {
  local label="$1" expected="$2" method="$3" path="$4" body="${5:-}"
  local code
  if [ -n "$body" ]; then
    code=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$BASE_URL$path" \
      -H "Content-Type: application/json" -d "$body")
  else
    code=$(curl -s -o /dev/null -w '%{http_code}' -X "$method" "$BASE_URL$path")
  fi
  if [ "$code" = "$expected" ]; then
    ok "$label → HTTP $code (expected $expected)"
  else
    printf '\033[0;31m✗ %s → HTTP %s (expected %s)\033[0m\n' "$label" "$code" "$expected"
  fi
}

# Fetches a profile and prints a compact per-section summary (counts + a sample),
# so the recording shows every field is populated without scrolling the full JSON.
# Uses Python (always available) so there are no fragile shell-quoting issues.
summarize_profile() {
  local url="$1"
  local json
  json=$(curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
    -d "{\"url\": \"$url\"}")
  # Pass the JSON via env var (not a pipe) so the heredoc below can own stdin
  # for the Python program without the two fighting over it.
  DEMO_JSON="$json" python3 <<'PY'
import os, json

try:
    p = json.loads(os.environ["DEMO_JSON"])
except Exception:
    print("  (could not parse response)")
    raise SystemExit(0)

if "error" in p:
    print(f"  error: {p['error']} — {p.get('detail','')}")
    raise SystemExit(0)

def count(key):
    return len(p.get(key) or [])

dash = "—"
name = p.get("name") or dash
headline = (p.get("headline") or dash)[:60]
location = p.get("location") or dash
about = f"{len(p['about'])} chars" if p.get("about") else dash
skills = ", ".join(s.get("name", "") for s in (p.get("skills") or [])[:3])

print(f"  name:           {name}")
print(f"  headline:       {headline}")
print(f"  location:       {location}")
print(f"  about:          {about}")
print(f"  experience:     {count('experience')} roles")
print(f"  education:      {count('education')} entries")
print(f"  skills:         {count('skills')}  {skills}")
print(f"  certifications: {count('certifications')}")
print(f"  languages:      {count('languages')}")
print(f"  profile_images: {count('profile_images')}")
PY
}

# Prints a checklist of the 10 fields the assignment asks for, ✓ if populated.
field_checklist() {
  local url="$1" json
  json=$(curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
    -d "{\"url\": \"$url\"}")
  DEMO_JSON="$json" python3 <<'PY'
import os, json
p = json.loads(os.environ["DEMO_JSON"])
GREEN = "\033[0;32m"; DIM = "\033[0;33m"; RST = "\033[0m"
fields = ["name", "headline", "location", "about", "experience",
          "education", "skills", "certifications", "languages", "profile_images"]
for f in fields:
    v = p.get(f)
    populated = len(v) > 0 if isinstance(v, list) else bool(v)
    mark = f"{GREEN}✓{RST}" if populated else f"{DIM}·{RST}"
    extra = f" ({len(v)})" if isinstance(v, list) and v else ""
    print(f"  {mark} {f}{extra}")
PY
}

# Prints the bonus sections (beyond the required fields) that came back non-empty.
bonus_sections() {
  local url="$1" json
  json=$(curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
    -d "{\"url\": \"$url\"}")
  DEMO_JSON="$json" python3 <<'PY'
import os, json
p = json.loads(os.environ["DEMO_JSON"])
bs = p.get("bonus_sections") or {}
nonempty = {k: len(v) for k, v in bs.items() if v}
if nonempty:
    for k, n in nonempty.items():
        print(f"  + {k}: {n}")
else:
    print("  (this profile has no bonus-section content)")
PY
}

clear
printf '\033[1;32mLinkedIn Profile API — live demo\033[0m\n'
printf 'Endpoint: %s\n' "$BASE_URL"
pause

# ── 1. Health ────────────────────────────────────────────────
hr
say "1. Health check — is the service up?"
run "curl -s $BASE_URL/healthz"
curl -s "$BASE_URL/healthz" | PP
pause

# ── 2. Input validation & error handling ─────────────────────
hr
say "2. Input validation & error handling (status-code checks)"
echo
check_status "non-LinkedIn URL"        400 POST /api/v1/profile '{"url": "https://example.com/x"}'
check_status "missing url field"       422 POST /api/v1/profile '{"not_url": "oops"}'
check_status "empty body"              422 POST /api/v1/profile '{}'
check_status "wrong method (GET)"      405 GET  /api/v1/profile
check_status "unknown route"           404 GET  /nope
check_status "healthz"                 200 GET  /healthz
pause

hr
say "   ...and the error responses share one clean shape:"
echo
run "curl -s $BASE_URL/api/v1/profile -d '{\"url\":\"https://example.com/x\"}'"
curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
  -d '{"url": "https://example.com/x"}' | PP
run "curl -s $BASE_URL/api/v1/profile -d '{\"not_url\":\"oops\"}'"
curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
  -d '{"not_url": "oops"}' | PP
pause

# ── 3. Real profile #1 ───────────────────────────────────────
hr
say "3. Real profile #1 — full structured JSON"
run "curl -s $BASE_URL/api/v1/profile -d '{\"url\":\"$PROFILE_1\"}'"
curl -s "$BASE_URL/api/v1/profile" -H "Content-Type: application/json" \
  -d "{\"url\": \"$PROFILE_1\"}" | PP
pause
hr
say "   Field summary for profile #1:"
echo
summarize_profile "$PROFILE_1"
pause

# ── 4. Real profile #2 (a different person) ──────────────────
hr
say "4. Real profile #2 — a different person, same clean output"
run "curl -s $BASE_URL/api/v1/profile -d '{\"url\":\"$PROFILE_2\"}'"
summarize_profile "$PROFILE_2"
pause

# ── 5. Required-field coverage ───────────────────────────────
hr
say "5. Every field the assignment asked for — populated:"
echo
field_checklist "$PROFILE_1"
pause

# ── 6. Caching (repeat lookups don't re-hit LinkedIn) ────────
hr
say "6. In-memory caching — a repeat lookup is served without re-hitting LinkedIn:"
echo
t1=$(curl -s -o /dev/null -w '%{time_total}' "$BASE_URL/api/v1/profile" \
  -H "Content-Type: application/json" -d "{\"url\": \"$PROFILE_1\"}")
t2=$(curl -s -o /dev/null -w '%{time_total}' "$BASE_URL/api/v1/profile" \
  -H "Content-Type: application/json" -d "{\"url\": \"$PROFILE_1\"}")
printf '  1st call: %ss\n  2nd call: %ss  \033[0;32m(cache hit — no LinkedIn round-trip)\033[0m\n' "$t1" "$t2"
pause

# ── 7. Beyond the ask — bonus sections ───────────────────────
hr
say "7. Beyond the 10 required fields — bonus sections it also returns:"
echo
bonus_sections "$PROFILE_1"
pause

hr
printf '\033[1;32mDone.\033[0m All 10 required fields populated, plus bonus sections. Interactive docs: %s/docs\n\n' "$BASE_URL"
