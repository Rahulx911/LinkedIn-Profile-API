"""
Fetches the profile page HTML directly (the same request resolve_urn() makes)
and searches it for a phrase from your own profile, to check whether LinkedIn
server-side-renders full profile content directly into the page HTML rather
than serving it via a separate XHR/fetch call.

    python scripts/inspect_html.py <public_identifier> "some distinctive phrase from your About/headline"

Saves the full HTML to debug_output/<public_identifier>.html (gitignored).
If the phrase is found, saves ~1000 chars of surrounding context to
debug_output/<public_identifier>.context.txt so you can open it locally and
see the actual structure (plain HTML text? a <script>/<code> JSON blob?
something else?) around your own data.

Run this yourself and inspect the output files locally — deliberately not
something that prints your profile content back into the chat.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print('Usage: python scripts/inspect_html.py <public_identifier> "search phrase"')
        sys.exit(1)

    public_identifier = sys.argv[1]
    phrase = sys.argv[2]

    settings = get_settings()
    client = VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )
    try:
        response = client._client.get(f"/in/{public_identifier}/")
    finally:
        client.close()

    out_dir = Path(__file__).resolve().parent.parent / "debug_output"
    out_dir.mkdir(exist_ok=True)

    html_path = out_dir / f"{public_identifier}.html"
    html_path.write_text(response.text)
    print(f"Status: {response.status_code}")
    print(f"Saved full HTML ({len(response.text)} chars) to {html_path}")

    idx = response.text.find(phrase)
    if idx == -1:
        print(f"\nPhrase not found in the HTML: {phrase!r}")
        print(
            "That would mean the content isn't server-rendered into the HTML "
            "either — it may only load via a call that requires JS execution "
            "to trigger (e.g. an IntersectionObserver-triggered lazy load), "
            "which would make it genuinely inaccessible without a browser."
        )
    else:
        start = max(0, idx - 500)
        end = min(len(response.text), idx + 500)
        context_path = out_dir / f"{public_identifier}.context.txt"
        context_path.write_text(response.text[start:end])
        print(f"\nFound at character offset {idx}.")
        print(f"Saved ~1000 chars of surrounding context to {context_path}")
        print(
            "Open that file locally and check: is it plain HTML text, or is "
            "it inside a <script>/<code> tag as part of a larger JSON blob? "
            "Tell me which, plus the tag's id attribute if it has one — no "
            "need to paste the actual content."
        )


if __name__ == "__main__":
    main()
