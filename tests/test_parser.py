import json
from pathlib import Path

from app.linkedin.parser import parse_profile

FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile_response.json"


def load_fixture() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parses_core_fields():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert profile.public_identifier == "janedoe"
    assert profile.name == "Jane Doe"
    assert profile.headline == "Software Engineer at ExampleCorp"
    assert profile.location == "Bengaluru, India"
    assert profile.about == "Building things with code."


def test_parses_experience():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert len(profile.experience) == 1
    exp = profile.experience[0]
    assert exp.title == "Software Engineer"
    assert exp.company == "ExampleCorp"
    assert exp.date_range.start == "2022-6"
    assert exp.date_range.end is None


def test_parses_education():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert len(profile.education) == 1
    edu = profile.education[0]
    assert edu.school == "Example University"
    assert edu.degree == "B.Tech"
    assert edu.date_range.start == "2018"
    assert edu.date_range.end == "2022"


def test_parses_skills_certifications_languages():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert [s.name for s in profile.skills] == ["Python", "FastAPI"]
    assert len(profile.certifications) == 1
    assert profile.certifications[0].name == "Example Certification"
    assert len(profile.languages) == 1
    assert profile.languages[0].name == "English"


def test_parses_profile_images():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert len(profile.profile_images) == 2
    assert profile.profile_images[0].url == (
        "https://media.licdn.com/dms/image/example/200x200.jpg"
    )


def test_handles_missing_sections_gracefully():
    minimal = {"included": [{"$type": "...Profile", "firstName": "Solo"}]}
    profile = parse_profile(minimal, public_identifier="solo")

    assert profile.name == "Solo"
    assert profile.experience == []
    assert profile.education == []
    assert profile.skills == []
