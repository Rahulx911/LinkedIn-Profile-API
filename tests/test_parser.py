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
EXPERIENCE_FLIGHT_THIRDPARTY3_FIXTURE = (
    Path(__file__).parent / "fixtures" / "experience_flight_thirdparty3_sample.txt"
)
EXPERIENCE_FLIGHT_THIRDPARTY4_FIXTURE = (
    Path(__file__).parent / "fixtures" / "experience_flight_thirdparty4_sample.txt"
)
EXPERIENCE_FLIGHT_THIRDPARTY5_FIXTURE = (
    Path(__file__).parent / "fixtures" / "experience_flight_thirdparty5_sample.txt"
)
EDUCATION_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "education_flight_sample.txt"
EDUCATION_FLIGHT_THIRDPARTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "education_flight_thirdparty_sample.txt"
)
SKILLS_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "skills_flight_sample.txt"
SKILLS_FLIGHT_THIRDPARTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "skills_flight_thirdparty_sample.txt"
)
ABOUT_FLIGHT_FIXTURE = Path(__file__).parent / "fixtures" / "about_flight_sample.txt"
ABOUT_FLIGHT_THIRDPARTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "about_flight_thirdparty_sample.txt"
)
ABOUT_FLIGHT_THIRDPARTY2_FIXTURE = (
    Path(__file__).parent / "fixtures" / "about_flight_thirdparty2_sample.txt"
)
PROFILE_HTML_FIXTURE = Path(__file__).parent / "fixtures" / "profile_html_sample.html"
PROFILE_HTML_THIRDPARTY_FIXTURE = (
    Path(__file__).parent / "fixtures" / "profile_html_thirdparty_sample.html"
)


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


def test_parses_skills_from_real_thirdparty_profile():
    # Real response captured live from a different person's profile.
    # Previously (see git history), skill names came only from a self-view
    # -only "Edit <Name> skill" edit-form landmark and always came back empty
    # on third-party profiles — same category of bug already fixed for
    # Experience and Education. Third-party profiles show an "Endorse <Name>"
    # button per skill instead (you can endorse others' skills but not your
    # own, and vice versa for editing).
    text = SKILLS_FLIGHT_THIRDPARTY_FIXTURE.read_text()
    skills = parse_skills_from_flight(text)

    names = [s.name for s in skills]
    assert names == [
        "Apache Kafka",
        "Test Automation",
        "Jenkins",
        "Kubernetes",
        "AngularJS",
        "Team Management",
        "Strategic Communications",
        "Event Management",
        "Brand Strategy",
        "Presentation Skills",
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


def test_parses_education_from_real_thirdparty_profile():
    # Real response captured live from a different person's profile (not the
    # account whose cookie made the request). Previously (see git history),
    # school name came from a self-view-only "Edit education <School>"
    # landmark and always came back null on third-party profiles; also
    # exercises a bare "YYYY - YYYY" date range (no month), which the
    # self-view fixture never has.
    text = EDUCATION_FLIGHT_THIRDPARTY_FIXTURE.read_text()
    education = parse_education_from_flight(text)

    assert len(education) == 3

    higher_secondary, high_school, university = education

    assert higher_secondary.school == "HOLY CHILD SR SEC SCHOOL"
    assert higher_secondary.degree == "Higher Secondary, CBSE (PCM with IP)"
    assert higher_secondary.date_range.start == "2019"
    assert higher_secondary.date_range.end == "2021"

    assert high_school.school == "HOLY CHILD SR SEC SCHOOL"
    assert high_school.degree == "High school, Cbse"
    assert high_school.date_range.start == "2017"
    assert high_school.date_range.end == "2019"

    assert university.school == "Vellore Institute of Technology"
    assert university.degree == "Bachelor of Technology - BTech, Information Technology"
    assert university.date_range.start == "Sep 2021"
    assert university.date_range.end == "Sep 2025"


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


def test_parses_experience_with_grouped_multirole_companies_sharing_one_type():
    # Real response captured live from a fourth profile — has TWO grouped
    # multi-role companies (JPMorganChase: 2 roles, Student Technical
    # Community: 2 roles) where every sub-role under the group shares the
    # SAME employment type, so LinkedIn shows it once in an aggregate summary
    # line ("Full-time · 1 yr 8 mos") instead of per role. Previously (see
    # git history) this broke two different ways: (1) a bare title directly
    # followed by its own date range (no per-role employment-type marker)
    # wasn't recognized as a title at all, so titles/companies/locations
    # shifted onto the wrong roles via the edit-landmark fallback; (2) the
    # combined "1 yr 8 mos" duration format wasn't matched by the
    # duration-badge regex (only single-unit forms like "8 mos" were), so the
    # grouped company's own name was never recognized as a company at all,
    # leaking the previous role's company into these instead.
    text = EXPERIENCE_FLIGHT_THIRDPARTY3_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)

    assert len(experiences) == 7

    (
        eng1,
        intern1,
        sponsorship,
        intern2,
        senior_core,
        junior_core,
        designer,
    ) = experiences

    assert eng1.title == "Software Engineer -1"
    assert eng1.company == "JPMorganChase"
    assert eng1.location == "Bengaluru, Karnataka, India · On-site"
    assert eng1.date_range.start == "Jul 2025"
    assert eng1.date_range.end is None

    assert intern1.title == "Software Engineer Intern"
    assert intern1.company == "JPMorganChase"
    assert intern1.date_range.start == "Jan 2025"
    assert intern1.date_range.end == "Jun 2025"
    assert intern1.description == "- CIB"

    assert sponsorship.title == "Head of Sponsorship"
    assert sponsorship.company == "graVITas VIT Vellore"
    assert sponsorship.location == "Vellore, Tamil Nadu, India"

    assert intern2.title == "Software Engineer Intern"
    assert intern2.company == "JPMorganChase"
    assert intern2.location == "Banglore · On-site"
    assert intern2.date_range.start == "Jun 2024"
    assert intern2.date_range.end == "Aug 2024"

    assert senior_core.title == "Senior core member"
    assert senior_core.company == "Student Technical Community — VIT Vellore"
    assert senior_core.date_range.start == "Oct 2022"
    assert senior_core.date_range.end == "Jun 2023"

    assert junior_core.title == "Junior core member"
    assert junior_core.company == "Student Technical Community — VIT Vellore"
    assert junior_core.date_range.start == "Dec 2021"
    assert junior_core.date_range.end == "Oct 2022"

    assert designer.title == "UI/UX Designer"
    assert designer.company == "Makoons Play School"
    assert designer.date_range.start == "Feb 2023"
    assert designer.date_range.end == "Mar 2023"
    # Bare country-only location ("India" — no comma, no On-site/Remote/
    # Hybrid suffix). Previously (see git history) unrecognized by
    # _is_location_token, so it came back null; worse, the literal word
    # leaked into the description as noise ("India\nI worked on..."). Fixed
    # by matching against an explicit country list.
    assert designer.location == "India"
    assert designer.description == (
        "I worked on this project to create a web app and website for Makoons "
        "Preschool. The focus was on designing a colorful and engaging user "
        "interface that would appeal to children. The outcome was a visually "
        "appealing web app and website with a vibrant color palette, "
        "interactive elements, and user-friendly navigation, providing an "
        "immersive experience for young children while effectively "
        "showcasing the preschool's offerings."
    )


def test_parses_experience_with_title_and_company_but_no_employment_type():
    # Real response captured live from a sixth profile — a role with NO
    # employment-type marker at all, neither inline per-role nor a grouped
    # aggregate line: bare title, then bare company, then straight to the
    # date range. Previously (see git history) this shape wasn't recognized
    # by either single-token rule (title-before-type/company-type/date, or
    # company-before-duration/aggregate), since BOTH tokens are themselves
    # unclassifiable until the date after the second one confirms it — the
    # company name ("The Developers Arena") was misread as the title, with
    # the real title silently dropped. Generalized into a run-length-2 rule
    # (see parse_experience_from_flight) rather than another special case,
    # verified here alongside every other real fixture with zero regressions.
    #
    # Known remaining gap, NOT fixed by this: this role's real description
    # ("Cleaned and processed 50,000+ records...") sits at a completely
    # different position in the raw stream (confirmed by direct inspection),
    # nowhere near this role's own date range — LinkedIn's own stream
    # ordering, not a heuristic gap. What gets captured instead is a skill
    # tag string ("Data Science and Jupyter") that happens to sit adjacent.
    # This assertion documents the current, known-imperfect behavior rather
    # than silently allowing it to regress further.
    text = EXPERIENCE_FLIGHT_THIRDPARTY4_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)

    assert len(experiences) == 3

    data_science_intern, ml_intern, data_analytics = experiences

    assert data_science_intern.title == "Data Science Intern"
    assert data_science_intern.company == "The Developers Arena"
    assert data_science_intern.date_range.start == "Nov 2025"
    assert data_science_intern.date_range.end == "Jan 2026"

    assert ml_intern.title == "Machine Learning Intern"
    assert ml_intern.company == "Unified Mentor Private Limited"
    assert ml_intern.location == "Gurugram, Haryana, India · Remote"
    assert ml_intern.date_range.start == "Dec 2025"
    assert ml_intern.date_range.end == "Feb 2026"
    assert "Built 3 end-to-end ML pipelines" in ml_intern.description

    assert data_analytics.title == "Data Science & Analytics"
    assert data_analytics.company == "Future Interns"
    assert data_analytics.location == "Remote"
    assert "During my internship at Future Interns" in data_analytics.description


def test_experience_does_not_leak_location_across_unrelated_companies():
    # Real response captured live from a seventh profile: a new single-role
    # company ("Tech Mahindra · Internship", a _COMPANY_TYPE_RE match) with
    # no location of its own, appearing right after a JPMorganChase role
    # that DID have one ("Bengaluru, Karnataka, India · On-site"). Previously
    # (see git history), current_location was only reset to None on the
    # bare-company rule, not this one, so Tech Mahindra incorrectly
    # inherited JPMorganChase's location instead of coming back null — the
    # real page shows no location for this role at all.
    text = EXPERIENCE_FLIGHT_THIRDPARTY5_FIXTURE.read_text()
    experiences = parse_experience_from_flight(text)
    by_title = {e.title: e for e in experiences}

    assert len(experiences) == 4
    assert by_title["Software Intern"].company == "Tech Mahindra"
    assert by_title["Software Intern"].location is None
    assert by_title["Summer Intern"].company == "JPMorganChase"
    assert by_title["Summer Intern"].location == "Bengaluru, Karnataka, India · On-site"


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


def test_parses_multi_paragraph_about_from_real_thirdparty_profile():
    # Real response captured live from a different person's profile — this
    # About has 5 separate paragraphs, each its own text token in the Flight
    # stream. Previously (see git history), picking a single "longest token"
    # returned only one paragraph, silently dropping the rest.
    text = ABOUT_FLIGHT_THIRDPARTY_FIXTURE.read_text()
    about = parse_about_from_flight(text)

    assert about == (
        "As a passionate coder and problem solver, I thrive on finding "
        "creative solutions to real-world challenges. With a keen eye for "
        "UI/UX design, I bring a unique blend of technical expertise and "
        "design sensibility to my work. I am an enthusiastic designer who "
        "enjoys creating user-centered and visually appealing experiences. "
        "In addition to my design skills, I am also proficient in backend "
        "web development. I have a strong command of programming languages, "
        "frameworks, and databases that allow me to build robust and "
        "scalable web applications. I actively participate in hackathons "
        "and stay updated with the latest advancements in the field of "
        "technology. My drive to constantly improve and my ability to work "
        "collaboratively make me a valuable asset to any project. Let's "
        "connect and collaborate to create meaningful and impactful "
        "solutions!"
    )


def test_parses_about_excluding_nearby_highlights_blurb():
    # Real response captured live from a fifth profile — the "Highlights"
    # mutual-education blurb ("You both studied at X from <date> to <date>")
    # sat only 7 tokens before the real About and got merged into the same
    # cluster, prepending unrelated text (see git history). This profile's
    # About is a single paragraph, so any leak is immediately visible in the
    # exact-match assertion below.
    text = ABOUT_FLIGHT_THIRDPARTY2_FIXTURE.read_text()
    about = parse_about_from_flight(text)

    assert about == (
        "Hi! I am Abhyudoy Chaki. A 21 Year Old B Tech Computer Science "
        "student at Vellore Institute of Technology. I am a passionate "
        "learner and I love to try out various fields of technology. "
        "Recently, I have developed an affinity towards understanding Data "
        "Science and Building Machine Learning Models. I am also actively "
        "working on Research Papers in Artificial Neural Network (ANN "
        "systems) and Genetic Algorithms. I also love doing Web "
        "Development. Having learned React, my web development skills are "
        "improving day by day, and I am also actively working on backend "
        "development. Apart from this, I am also interested in Android "
        "Development in Java and I have created apps that reflect my keen "
        "interest. I wish to learn more, work with and deliver best "
        "technologies to the world!"
    )
    assert "You both studied" not in about


def test_finds_correct_mini_profile_when_multiple_are_present():
    # A subresource can embed more than one MiniProfile — e.g. co-authors on
    # a publication, each their own Contributor->MiniProfile. Real bug (see
    # git history): picking the first one found returned a co-author's
    # occupation ("--") and profile photo instead of the profile owner's.
    # The wrong one is inserted FIRST here to prove selection is by
    # publicIdentifier match, not list order.
    fixture = load_fixture()
    fixture["subresources"]["projects"]["included"].insert(
        0,
        {
            "$type": "com.linkedin.voyager.identity.shared.MiniProfile",
            "firstName": "Co",
            "lastName": "Author",
            "occupation": "--",
            "publicIdentifier": "some-coauthor",
            "picture": {
                "rootUrl": "https://media.licdn.com/dms/image/coauthor/",
                "artifacts": [
                    {"width": 200, "height": 200, "fileIdentifyingUrlPathSegment": "200x200.jpg"}
                ],
            },
        },
    )
    profile = parse_profile(fixture, public_identifier="janedoe")

    assert profile.headline == "Software Engineer at ExampleCorp"
    assert profile.profile_images[0].url == (
        "https://media.licdn.com/dms/image/example/200x200.jpg"
    )


def test_falls_back_to_first_mini_profile_when_none_match_identifier():
    # If no MiniProfile's publicIdentifier matches (shouldn't normally
    # happen, but the subresource data isn't a documented contract), fall
    # back to the first one found rather than returning nothing — matches
    # the original opportunistic-recovery behavior when there's no better
    # signal available.
    fixture = load_fixture()
    profile = parse_profile(fixture, public_identifier="not-in-the-fixture-at-all")

    assert profile.headline == "Software Engineer at ExampleCorp"


def test_extracts_location_skipping_connection_degree_badge_false_positive():
    # Real page HTML excerpt captured live from a different person's profile
    # — a mutual-connections widget's "· 1st"/"· 2nd" degree badge matches
    # the same <p>·</p><div><p> shape as the real top card and sits earlier
    # in the page. Previously (see git history), a bare `[^<]*·` matched
    # that badge first and returned "· 2nd" as the location instead of the
    # real "India" further down the page.
    html = PROFILE_HTML_THIRDPARTY_FIXTURE.read_text()

    assert _extract_location_from_html(html) == "India"
