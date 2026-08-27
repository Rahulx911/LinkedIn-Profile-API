"""
Prints every parsed chunk's id and a preview of its value/type, to inspect
the raw Flight chunk structure directly.

    python scripts/debug_chunks.py <path> [min_len]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.linkedin.flight import parse_chunks  # noqa: E402


def main() -> None:
    path = sys.argv[1]
    min_len = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    text = Path(path).read_text()
    chunks = parse_chunks(text)
    for cid, value in chunks.items():
        if isinstance(value, str):
            if len(value) < min_len:
                continue
            print(f"[{cid}] str(len={len(value)}): {value[:200]!r}")
        else:
            print(f"[{cid}] {type(value).__name__}")


if __name__ == "__main__":
    main()
