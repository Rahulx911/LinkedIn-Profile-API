"""
Probes whether /certifications supports pagination query params, and if so,
which ones. Confirmed live: a profile with 12 real certifications only gets
6 back from a plain GET (see README "Known limitations") — this checks
whether that's because of a small server-side default that a larger
explicit `count` can override, or because it needs `start`-based paging
like Skills' SDUI pagination action does (a different mechanism entirely).

    python scripts/probe_certifications_pagination.py <public_identifier>

Run this against a profile with MORE than 6 real certifications (Certification
entities specifically — the endpoint's response also includes MiniCompany
issuer references mixed into the same "included" list, so compare the
certification-only count, not the raw list length) for a meaningful result.
Prints the certification count and names for each variant tried.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402


def cert_names(raw: dict | None) -> list[str]:
    if not raw:
        return []
    return [
        e.get("name")
        for e in raw.get("included", [])
        if "certification" in str(e.get("$type", "")).lower()
    ]


VARIANTS = {
    "baseline (no params)": "",
    "start=0&count=25": "?start=0&count=25",
    "count=25": "?count=25",
    "start=6&count=10": "?start=6&count=10",
    "start=10&count=10": "?start=10&count=10",
}


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/probe_certifications_pagination.py <public_identifier>")
        sys.exit(1)

    public_identifier = sys.argv[1]
    settings = get_settings()
    client = VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )

    try:
        for label, query in VARIANTS.items():
            path = f"/voyager/api/identity/profiles/{public_identifier}/certifications{query}"
            try:
                response = client._client.get(path)
                if response.status_code != 200:
                    print(f"{label:25s} -> HTTP {response.status_code}")
                    continue
                names = cert_names(response.json())
                print(f"{label:25s} -> {len(names)} certifications")
                for n in names:
                    print(f"{'':25s}    - {n}")
            except Exception as e:
                print(f"{label:25s} -> ERROR {e}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
