"""
Prints the token stream (via app.linkedin.flight.extract_text_stream, the
same non-deduped extractor the real parser uses) with index numbers, so exact
adjacency around a given token can be inspected precisely.

    python scripts/debug_tokens.py debug_output/rahul911.experience.sdui.txt Delhivery
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.linkedin.flight import extract_text_stream  # noqa: E402


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/debug_tokens.py <path> <token-to-find>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text()
    target = sys.argv[2]
    tokens = extract_text_stream(text)
    print(f"Total tokens: {len(tokens)}\n")

    for idx, tok in enumerate(tokens):
        if tok == target:
            lo, hi = max(0, idx - 3), min(len(tokens), idx + 10)
            print(f"--- match at index {idx} ---")
            for k in range(lo, hi):
                marker = ">>" if k == idx else "  "
                print(f"{marker} [{k}] {tokens[k]!r}")
            print()


if __name__ == "__main__":
    main()
