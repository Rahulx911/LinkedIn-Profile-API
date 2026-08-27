"""
Runs the full extraction pipeline (VoyagerClient.fetch_all_raw + parse_profile)
against a real profile and prints the final parsed result, so you can sanity
check it end-to-end.

    python scripts/inspect_raw.py <public_identifier>
    (e.g. python scripts/inspect_raw.py rahul911)

Saves the raw bundle (HTML + subresource JSON) to
debug_output/<public_identifier>.raw.json (gitignored).

This is deliberately a manual, human-run script rather than something an
automated agent invokes — it's making real authenticated calls to LinkedIn
with your session cookie, and that's a step only you should trigger.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402
from app.linkedin.parser import parse_profile  # noqa: E402


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
        raw = client.fetch_all_raw(public_identifier)
    finally:
        client.close()

    print("Subresource availability:")
    for resource, value in raw["subresources"].items():
        count = len(value.get("included", [])) if value else 0
        print(f"  {resource:24s} {'available' if value else 'unavailable (404/410)':24s} ({count} entities)")

    profile = parse_profile(raw, public_identifier)
    print("\nParsed profile:")
    print(profile.model_dump_json(indent=2))

    out_dir = Path(__file__).resolve().parent.parent / "debug_output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{public_identifier}.raw.json"
    out_path.write_text(json.dumps(raw, indent=2))
    print(f"\nFull raw bundle saved to {out_path} (gitignored, do not commit).")


if __name__ == "__main__":
    main()
