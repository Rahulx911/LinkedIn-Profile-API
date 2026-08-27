import json
from pathlib import Path

from app.linkedin.parser import parse_profile

FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile_response.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parses_name_from_title():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert profile.public_identifier == "janedoe"
    assert profile.name == "Jane Doe"


def test_parses_headline_and_images_from_incidental_mini_profile():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert profile.headline == "Software Engineer at ExampleCorp"
    assert len(profile.profile_images) == 2
    assert profile.profile_images[0].url == (
        "https://media.licdn.com/dms/image/example/200x200.jpg"
    )
    assert profile.profile_images[0].width == 200


def test_parses_certifications():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert len(profile.certifications) == 1
    cert = profile.certifications[0]
    assert cert.name == "Example Certification"
    assert cert.issuer == "Example Institute"
    assert cert.issued_date == "2023-1"
    assert cert.credential_id == "ABC123"


def test_parses_languages():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert len(profile.languages) == 1
    assert profile.languages[0].name == "English"
    assert profile.languages[0].proficiency == "Native or bilingual"


def test_parses_bonus_projects_excluding_reference_entities():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    projects = profile.bonus_sections["projects"]
    assert len(projects) == 1
    assert projects[0].title == "Example Project"
    assert projects[0].description == "Built a thing."
    assert projects[0].date_range.start == "2022-1"
    assert projects[0].date_range.end == "2022-6"


def test_bonus_subtitle_drops_unresolved_urn_references():
    fixture = load_fixture()
    fixture["subresources"]["projects"]["included"].append(
        {
            "$type": "com.linkedin.voyager.identity.profile.Project",
            "title": "Project With Urn Occupation",
            "occupation": "urn:li:fs_education:(FAKEURN,123)",
        }
    )
    profile = parse_profile(fixture, public_identifier="janedoe")

    urn_project = next(
        p for p in profile.bonus_sections["projects"] if p.title == "Project With Urn Occupation"
    )
    assert urn_project.subtitle is None


def test_experience_education_skills_always_empty():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert profile.experience == []
    assert profile.education == []
    assert profile.skills == []


def test_handles_missing_mini_profile_gracefully():
    minimal = {
        "html": "<html><head><title>Solo | LinkedIn</title></head></html>",
        "subresources": {"certifications": None, "languages": None},
    }
    profile = parse_profile(minimal, public_identifier="solo")

    assert profile.name == "Solo"
    assert profile.headline is None
    assert profile.profile_images == []
    assert profile.certifications == []
    assert profile.languages == []


def test_handles_missing_title_gracefully():
    minimal = {"html": "<html><head></head></html>", "subresources": {}}
    profile = parse_profile(minimal, public_identifier="solo")

    assert profile.name is None
