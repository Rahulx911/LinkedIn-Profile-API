"""
Run this locally, with your own LI_AT_COOKIE set in .env, to see the real shape
of LinkedIn's response and tune app/linkedin/parser.py against it.

    python scripts/inspect_raw.py <public_identifier>
    (e.g. python scripts/inspect_raw.py rahul911)

It prints every distinct `$type` found in the response's `included` array, and
saves the full raw JSON to debug_output/<public_identifier>.raw.json (gitignored)
so you can open it and check real field names.

This is deliberately a manual, human-run script rather than something an
automated agent invokes — it's making a real authenticated call to LinkedIn
with your session cookie, and that's a step only you should trigger.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/inspect_raw.py <public_identifier>")
        sys.exit(1)

    public_identifier = sys.argv[1]
    settings = get_settings()
    client = VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )

    try:
        raw = client.get_profile_raw(public_identifier)
    finally:
        client.close()

    print(f"Top-level response keys: {sorted(raw.keys())}\n")

    included = raw.get("included", [])
    if included:
        types = sorted({str(item.get("$type", "<missing>")) for item in included})
        print(f"{len(included)} entities in `included`. Distinct $type values:\n")
        for t in types:
            print(f"  - {t}")
    else:
        print(
            "No `included` array in the response — this looks like the legacy "
            "profileView shape, which nests data directly (e.g. raw['positions'], "
            "raw['educations']) instead of a flat normalized list. Check "
            "debug_output/*.raw.json to see the actual structure and adjust "
            "app/linkedin/parser.py accordingly."
        )

    out_dir = Path(__file__).resolve().parent.parent / "debug_output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{public_identifier}.raw.json"
    out_path.write_text(json.dumps(raw, indent=2))
    print(f"\nFull raw response saved to {out_path} (gitignored, do not commit).")


if __name__ == "__main__":
    main()
