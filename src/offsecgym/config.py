"""Process configuration. Secrets are supplied by environment, never spec files."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OFFSECGYM_", extra="ignore")

    database_url: str | None = None
    log_level: str = "INFO"
