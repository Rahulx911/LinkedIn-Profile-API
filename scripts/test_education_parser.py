"""
Tests parse_education_from_flight() against an already-saved SDUI response
file (from scripts/inspect_education.py), without needing a fresh live fetch.

    python scripts/test_education_parser.py debug_output/rahul911.education.sdui.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.linkedin.parser import parse_education_from_flight  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_education_parser.py <path-to-sdui-response.txt>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text()
    education = parse_education_from_flight(text)

    print(f"Parsed {len(education)} education entries:\n")
    for edu in education:
        print(edu.model_dump_json(indent=2))
        print()


if __name__ == "__main__":
    main()
