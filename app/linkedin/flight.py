"""
Minimal parser for React Server Components "Flight" wire format responses, as
returned by LinkedIn's SDUI "component" action (see
VoyagerClient.fetch_sdui_component_raw and README "How this was reverse
engineered"). This is not a general-purpose Flight decoder — just enough to
resolve chunk cross-references and walk the tree for visible text.

Format: line-delimited `<id>:<payload>`, where payload is either a module
import declaration (`I[...]`, not data) or a JSON value. Values can reference
another chunk via a `"$<id>"` string. A `"$L<id>"` string is a *different*
kind of reference (to a reusable client component definition, not plain
data) — resolving those the same way as `$<id>` causes serious duplication,
since the same component chunk gets referenced from many call sites with
different (unmodeled, per-instance) props; left unresolved deliberately.
"""

import json
import re

_LINE_PATTERN = re.compile(r"^([0-9a-fA-F]+):(.*)$")
_REF_PATTERN = re.compile(r"^\$([0-9a-fA-F]+)$")


def parse_chunks(text: str) -> dict[str, object]:
    chunks: dict[str, object] = {}
    for line in text.splitlines():
        match = _LINE_PATTERN.match(line)
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


def _resolve(value, chunks: dict, seen: frozenset = frozenset()):
    if isinstance(value, str):
        match = _REF_PATTERN.match(value)
        if match and match.group(1) in chunks and match.group(1) not in seen:
            return _resolve(chunks[match.group(1)], chunks, seen | {match.group(1)})
        return value
    if isinstance(value, list):
        return [_resolve(v, chunks, seen) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, chunks, seen) for k, v in value.items()}
    return value


def _collect_children_text(value, out: list[str]) -> None:
    if isinstance(value, dict):
        if "children" in value:
            _collect_children_text(value["children"], out)
        for key, v in value.items():
            if key != "children":
                _collect_children_text(v, out)
    elif isinstance(value, list):
        for item in value:
            _collect_children_text(item, out)
    elif isinstance(value, str):
        s = value.strip()
        if s:
            out.append(s)


def extract_text_stream(text: str) -> list[str]:
    """Visible text fragments in render order, WITHOUT deduping — repeated
    text (e.g. two roles with the same job title) is meaningful here."""
    chunks = parse_chunks(text)
    out: list[str] = []
    for value in chunks.values():
        _collect_children_text(_resolve(value, chunks), out)
    return out
