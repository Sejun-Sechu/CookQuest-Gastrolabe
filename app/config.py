"""Shared discovery/map storage. Preserve the legacy database location."""
from functools import lru_cache
from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    cache_enabled: bool = Field(default=False, validation_alias="COOKQUEST_CACHE_ENABLED")
    database_path: Path = Field(
        default=Path("data/cooking.db"),
        validation_alias=AliasChoices("COOKQUEST_DATABASE_PATH", "COOKING_DATABASE_PATH"),
    )
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()

