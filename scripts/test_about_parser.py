"""
Tests parse_about_from_flight() against an already-saved SDUI response file.

    python scripts/test_about_parser.py debug_output/rahul911.above_activity.sdui.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.linkedin.parser import parse_about_from_flight  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_about_parser.py <path-to-sdui-response.txt>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text()
    about = parse_about_from_flight(text)
    print(f"about: {about!r}")


if __name__ == "__main__":
    main()
