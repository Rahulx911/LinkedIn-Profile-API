"""
Probes a handful of classic per-section Voyager REST endpoints to check
whether any survived the retirement of the combined /profileView endpoint
(confirmed 410 Gone). Cheap way to check before chasing the SDUI lazy-load
query further.

    python scripts/probe_endpoints.py <public_identifier>

Prints status code for each candidate. For any 200, saves the body to
debug_output/probe_<name>.json (gitignored) so you can inspect it locally.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.config import get_settings  # noqa: E402
from app.linkedin.client import VoyagerClient  # noqa: E402

CANDIDATES = {
    "position_singular": "/voyager/api/identity/profiles/{id}/position",
    "education_singular": "/voyager/api/identity/profiles/{id}/education",
    "skill_singular": "/voyager/api/identity/profiles/{id}/skill",
    "skillCategory": "/voyager/api/identity/profiles/{id}/skillCategory",
    "volunteerExperiences": "/voyager/api/identity/profiles/{id}/volunteerExperiences",
    "projects": "/voyager/api/identity/profiles/{id}/projects",
    "publications": "/voyager/api/identity/profiles/{id}/publications",
    "honors": "/voyager/api/identity/profiles/{id}/honors",
    "courses": "/voyager/api/identity/profiles/{id}/courses",
    "testScores": "/voyager/api/identity/profiles/{id}/testScores",
    "patents": "/voyager/api/identity/profiles/{id}/patents",
    "organizations": "/voyager/api/identity/profiles/{id}/organizations",
}


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/probe_endpoints.py <public_identifier>")
        sys.exit(1)

    public_identifier = sys.argv[1]
    settings = get_settings()
    client = VoyagerClient(
        li_at_cookie=settings.li_at_cookie,
        jsessionid=settings.jsessionid,
        timeout=settings.linkedin_request_timeout_seconds,
    )

    out_dir = Path(__file__).resolve().parent.parent / "debug_output"
    out_dir.mkdir(exist_ok=True)

    try:
        for name, path_template in CANDIDATES.items():
            path = path_template.format(id=public_identifier)
            try:
                response = client._client.get(path)
                print(f"{name:20s} {response.status_code:4d}  {path}")
                if response.status_code == 200:
                    out_path = out_dir / f"probe_{name}.json"
                    out_path.write_text(json.dumps(response.json(), indent=2))
                    print(f"{'':20s} -> saved to {out_path}")
            except Exception as e:
                print(f"{name:20s} ERR  {e}")
    finally:
        client.close()


if __name__ == "__main__":
    main()
