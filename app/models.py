from pydantic import BaseModel


class DateRange(BaseModel):
    start: str | None = None
    end: str | None = None


class Experience(BaseModel):
    title: str | None = None
    company: str | None = None
    location: str | None = None
    date_range: DateRange | None = None
    description: str | None = None


class Education(BaseModel):
    school: str | None = None
    degree: str | None = None
    field_of_study: str | None = None
    date_range: DateRange | None = None
    description: str | None = None


class Skill(BaseModel):
    name: str


class Certification(BaseModel):
    name: str | None = None
    issuer: str | None = None
    issued_date: str | None = None
    credential_id: str | None = None
    credential_url: str | None = None


class Language(BaseModel):
    name: str
    proficiency: str | None = None


class ProfileImage(BaseModel):
    url: str
    width: int | None = None
    height: int | None = None


class BonusItem(BaseModel):
    title: str | None = None
    subtitle: str | None = None
    description: str | None = None
    date_range: DateRange | None = None
    url: str | None = None


class ProfileResponse(BaseModel):
    public_identifier: str
    name: str | None = None
    headline: str | None = None
    location: str | None = None
    about: str | None = None
    experience: list[Experience] = []
    education: list[Education] = []
    skills: list[Skill] = []
    certifications: list[Certification] = []
    languages: list[Language] = []
    profile_images: list[ProfileImage] = []
    # Sections beyond what the assignment asked for, surfaced because the
    # underlying LinkedIn endpoints for them are still live — see README.
    # Keyed by section name (e.g. "projects", "honors"); empty if none found.
    bonus_sections: dict[str, list[BonusItem]] = {}


class ProfileRequest(BaseModel):
    url: str


class ErrorResponse(BaseModel):
    error: str
    detail: str
