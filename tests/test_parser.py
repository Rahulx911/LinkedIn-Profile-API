import json
from pathlib import Path

from app.linkedin.parser import (
    _extract_location_from_html,
    parse_about_from_flight,
    parse_education_from_flight,
    parse_experience_from_flight,
    parse_profile,
    parse_skills_from_flight,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sample_profile_response.json"
EXPERIENCE_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "experience_flight_sample.txt"
EXPERIENCE_FLIGHT_THIRDPARTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "experience_flight_thirdparty_sample.txt"
)
EXPERIENCE_FLIGHT_THIRDPARTY2_FIXTURE = (
    Path(__file__).parent / "fixtures" / "experience_flight_thirdparty2_sample.txt"
)
EDUCATION_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "education_flight_sample.txt"
SKILLS_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "skills_flight_sample.txt"
ABOUT_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "about_flight_sample.txt"
PROFILE_HTML_FIXTURE = Path(__file__).parent / "fixtures" / "profile_html_sample.html"


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


def test_experience_education_skills_empty_without_flight_data():
    profile = parse_profile(load_fixture(), public_identifier="janedoe")

    assert profile.experience == []
    assert profile.education == []
    assert profile.skills == []


def test_parses_skills_from_real_captured_flight_response():
    # Real response captured live (see README) from the experimental SDUI
    # "pagination" action — not synthetic. Covers one page (10) of skills;
    # LinkedIn paginates further pages that aren't fetched — see README.
    text = SKILLS_FLIGHT_FIXTURE.read_text()
    skills = parse_skills_from_flight(text)

    names = [s.name for s in skills]
    assert names == [
        "LangGraph",
        "Voyage embeddings",
        "PostgreSQL",
        "Qdrant",
        "Terraform",
        "Docker",
        "Amazon Simple Notification Service (SNS)",
        "Amazon Web Services (AWS)",
        "Go (Programming Language)",
        "Distributed Systems",
    ]


def test_skills_parser_never_raises_on_garbage_input():
    assert parse_skills_from_flight("not a real flight response") == []
    assert parse_skills_from_flight("") == []
    assert parse_skills_from_flight(None) == []


def test_parses_education_from_real_captured_flight_response():
    # Real response captured live (see README) from the experimental SDUI
    # "pagination" action — not synthetic, unlike the other fixtures.
    text = EDUCATION_FLIGHT_FIXTURE.read_text()
    education = parse_education_from_flight(text)

    assert len(education) == 3

    uni, junior_college, school = education

    assert uni.school == "Vellore Institute of Technology"
    assert uni.degree == "Bachelor of Technology - BTech, Computer Science"
    assert uni.date_range.start == "Jul 2021"
    assert uni.date_range.end == "Sep 2025"

    assert junior_college.school == "Shri Pramod Patil Junior college"
    assert junior_college.degree == "12 complete, science"
    assert junior_college.date_range.start == "Jul 2019"
    assert junior_college.date_range.end == "Jan 2021"

    assert school.school == "Wisdom High International School - India"
    assert school.degree == "10th, Isce"
    assert school.date_range.start == "Jul 2007"
    assert school.date_range.end == "Mar 2019"


def test_education_parser_never_raises_on_garbage_input():
    assert parse_education_from_flight("not a real flight response") == []
    assert parse_education_from_flight("") == []
    assert parse_education_from_flight(None) == []


def test_parses_experience_from_real_captured_flight_response():
    # Real response captured live (see README "How this was reverse
    # engineered") from the experimental SDUI "component" action — not
    # synthetic, unlike the other fixtures. Multi-role-same-company
    # (Delhivery) and single-role (Prabha, Satyam) layouts differ in the
    # underlying wire format, so this exercises both code paths at once.
    text = EXPERIENCE_FLIGHT_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)

    assert len(experiences) == 4

    role1, role2, role3, role4 = experiences

    assert role1.title == "Software Developer"
    assert role1.company == "Delhivery"
    assert role1.location == "Pune District, Maharashtra, India · On-site"
    assert role1.date_range.start == "Jul 2026"
    assert role1.date_range.end is None  # "Present"

    assert role2.title == "Software Developer"
    assert role2.company == "Delhivery"
    assert role2.date_range.start == "Nov 2025"
    assert role2.date_range.end == "Jul 2026"

    assert role3.title == "Information Technology Intern"
    assert role3.company == "Prabha Industries - India"
    assert role3.location == "Bengaluru, Karnataka, India · On-site"
    assert role3.date_range.start == "Jun 2024"
    assert role3.date_range.end == "Aug 2024"
    assert "Designed and developed a comprehensive company website" in role3.description

    assert role4.title == "Information Technology Intern"
    assert role4.company == "Satyam Technocrats"
    assert role4.location == "Nashik, Maharashtra, India · On-site"
    assert "I had the opportunity to work as an I.T Intern" in role4.description


def test_parses_experience_from_real_thirdparty_profile():
    # Real response captured live from a DIFFERENT person's profile (not the
    # account whose cookie made the request) — see README "Validated against
    # a genuinely different, third-party profile". Exercises layout
    # differences self-view never shows: inline title text (no edit-form
    # landmark), a single-date-no-range role ("Aug 2026 · 1 mo"), bare
    # "Remote" with no city prefix, and a bare city/state/country address
    # with no workplace-type suffix.
    text = EXPERIENCE_FLIGHT_THIRDPARTY_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)

    assert len(experiences) == 3

    role1, role2, role3 = experiences

    assert role1.title == "Computer vision intern"
    assert role1.company == "MacV AI"
    assert role1.location == "Remote"
    assert role1.date_range.start == "Jan 2026"
    assert role1.date_range.end is None

    assert role2.title == "Computer Vision Engineer"
    assert role2.company == "MacV AI"
    assert role2.location == "Remote"
    assert role2.date_range.start == "Aug 2026"
    assert role2.date_range.end is None

    assert role3.title == "Computer vision intern"
    assert role3.company == "Pramana"
    assert role3.location == "Bengaluru, Karnataka, India"
    assert role3.date_range.start == "May 2025"
    assert role3.date_range.end == "Dec 2025"
    assert role3.description is None


def test_parses_experience_from_real_thirdparty_profile_with_attachments():
    # Real response captured live from a third profile (see README) — this
    # one has PDF attachment thumbnails and a "LinkedIn helped me get this
    # job" promo banner mixed into the raw text stream, both of which must be
    # filtered as noise rather than leaking into (or misattributed between)
    # role descriptions. Matched by company rather than list position: the
    # underlying data stream's order doesn't match the page's visual order
    # (a known, documented limitation) even though each role's own fields are
    # still correctly grouped together.
    text = EXPERIENCE_FLIGHT_THIRDPARTY2_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)
    by_company = {e.company: e for e in experiences}

    assert len(experiences) == 5

    hyperspec = by_company["Hyperspec AI"]
    assert hyperspec.title == "Robotics Developer Internship"
    assert hyperspec.location == "San Francisco, California, United States · Remote"
    assert hyperspec.date_range.start == "Oct 2023"
    assert hyperspec.date_range.end is None
    assert hyperspec.description is None  # promo banner filtered, not leaked

    delhivery = by_company["Delhivery"]
    assert delhivery.title == "Robotics Developer 1"
    assert delhivery.date_range.start == "Dec 2025"
    assert delhivery.description is None

    miko = by_company["Miko"]
    assert miko.title == "Robotics Engineer 1"
    assert miko.date_range.start == "Jul 2024"
    assert miko.date_range.end == "Dec 2025"
    # Previously leaked Hyperspec's attachment filename here — must be gone.
    assert miko.description is None

    amazon = by_company["Amazon"]
    assert amazon.title == "Amazon ML Summer School"
    assert "2 week long mentorship program" in amazon.description

    mercedes = by_company["Mercedes-Benz Research and Development India"]
    assert mercedes.title == "Student Trainee Internship"
    assert "Developed self-driving car software" in mercedes.description


def test_experience_parser_never_raises_on_garbage_input():
    assert parse_experience_from_flight("not a real flight response") == []
    assert parse_experience_from_flight("") == []
    assert parse_experience_from_flight(None) == []


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


def test_parses_about_from_real_captured_flight_response():
    # Real response captured live (see README) from the experimental SDUI
    # "component" action (componentId: profileCardsAboveActivity) — not
    # synthetic, unlike the other fixtures.
    text = ABOUT_FLIGHT_FIXTURE.read_text()
    about = parse_about_from_flight(text)

    assert about == (
        "CS 2025 graduate passionate about building production-grade AI "
        "systems. Currently building computer vision pipelines and "
        "ML-integrated microservices at Delhivery. Semi-finalist at "
        "Flipkart Grid 6.0."
    )


def test_about_parser_never_raises_on_garbage_input():
    assert parse_about_from_flight("not a real flight response") is None
    assert parse_about_from_flight("") is None
    assert parse_about_from_flight(None) is None


def test_extracts_location_from_real_captured_html():
    # Real page HTML excerpt captured live (see README) — the "<Company> ·
    # <School>" line followed immediately by the location <p>, copied
    # verbatim (real class names/structure intact).
    html = PROFILE_HTML_FIXTURE.read_text()

    assert _extract_location_from_html(html) == "India"


def test_location_parser_never_raises_on_garbage_input():
    assert _extract_location_from_html("not real html") is None
    assert _extract_location_from_html("") is None
