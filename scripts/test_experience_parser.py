"""
Tests parse_experience_from_flight() against an already-saved SDUI response
file (from scripts/inspect_sdui.py), without needing a fresh live fetch.

    python scripts/test_experience_parser.py debug_output/rahul911.experience.sdui.txt
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.linkedin.parser import parse_experience_from_flight  # noqa: E402


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/test_experience_parser.py <path-to-sdui-response.txt>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text()
    experiences = parse_experience_from_flight(text)

    print(f"Parsed {len(experiences)} experience entries:\n")
    for exp in experiences:
        print(exp.model_dump_json(indent=2))
        print()


if __name__ == "__main__":
    main()
