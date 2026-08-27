"""
Tests VoyagerClient.fetch_sdui_skills_raw() — the experimental "pagination"
RSC action for the Skills section. Saves the raw response (React Server
Components "Flight" wire format) to a local file.

    python scripts/inspect_skills.py <public_identifier> <profile_id>
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/inspect_skills.py <public_identifier> <profile_id>")
        sys.exit(1)

    public_identifier, profile_id = sys.argv[1], sys.argv[2]

    settings = get_settings()
    client = VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )
    try:
        text = client.fetch_sdui_skills_raw(public_identifier, profile_id)
    finally:
        client.close()

    out_dir = Path(__file__).resolve().parent.parent / "debug_output"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{public_identifier}.skills.sdui.txt"
    out_path.write_text(text)

    print(f"Response length: {len(text)} chars")
    print(f"Saved to {out_path}")


if __name__ == "__main__":
    main()
