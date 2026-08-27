from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    li_at_cookie: str
    jsessionid: str | None = None

    profile_cache_ttl_seconds: int = 3600
    linkedin_request_timeout_seconds: float = 15.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
