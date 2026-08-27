"""
Parses a React Server Components "Flight" protocol response (as saved by
scripts/inspect_sdui.py) and extracts visible text in render order.

Flight responses are line-delimited: each line is `<id>:<payload>`, where
payload is either an import declaration (`I[...]`, skipped) or a JSON value.
Values can reference other chunks by id via a `"$<id>"` string — this script
resolves those references, then walks the resolved tree collecting every
string found under a `children` key (mirroring how React element props work),
which reconstructs something close to the rendered text content.

    python scripts/parse_sdui_flight.py debug_output/rahul911.experience.sdui.txt
"""

import json
import re
import sys
from pathlib import Path

LINE_PATTERN = re.compile(r"^([0-9a-fA-F]+):(.*)$")
REF_PATTERN = re.compile(r"^\$([0-9a-fA-F]+)$")


def parse_flight(text: str) -> dict[str, object]:
    chunks: dict[str, object] = {}
    for line in text.splitlines():
        match = LINE_PATTERN.match(line)
        if not match:
            continue
        cid, rest = match.groups()
        if rest.startswith("I["):
            continue  # module import declaration, not data
        try:
            chunks[cid] = json.loads(rest)
        except json.JSONDecodeError:
            chunks[cid] = rest
    return chunks


def resolve(value, chunks: dict, seen: frozenset = frozenset()):
    if isinstance(value, str):
        match = REF_PATTERN.match(value)
        if match and match.group(1) in chunks and match.group(1) not in seen:
            return resolve(chunks[match.group(1)], chunks, seen | {match.group(1)})
        return value
    if isinstance(value, list):
        return [resolve(v, chunks, seen) for v in value]
    if isinstance(value, dict):
        return {k: resolve(v, chunks, seen) for k, v in value.items()}
    return value


def extract_children_text(value, out: list[str]) -> None:
    if isinstance(value, dict):
        if "children" in value:
            extract_children_text(value["children"], out)
        for key, v in value.items():
            if key != "children":
                extract_children_text(v, out)
    elif isinstance(value, list):
        for item in value:
            extract_children_text(item, out)
    elif isinstance(value, str):
        s = value.strip()
        if s:
            out.append(s)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/parse_sdui_flight.py <path-to-sdui-response.txt>")
        sys.exit(1)

    text = Path(sys.argv[1]).read_text()
    chunks = parse_flight(text)
    print(f"Parsed {len(chunks)} chunks.")

    out: list[str] = []
    for cid, value in chunks.items():
        resolved = resolve(value, chunks)
        extract_children_text(resolved, out)

    seen = set()
    deduped = []
    for s in out:
        if s not in seen:
            deduped.append(s)
            seen.add(s)

    print(f"\nExtracted {len(deduped)} distinct text fragments, in order:\n")
    for s in deduped:
        print(s)


if __name__ == "__main__":
    main()
