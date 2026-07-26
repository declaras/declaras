"""Configuracion por entorno. Todo se inyecta por variables DECLARAS_*."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"


class StorageBackend(StrEnum):
    LOCAL = "local"
    GCS = "gcs"


class DianAdapterKind(StrEnum):
    FAKE = "fake"
    HTTP = "http"
    PLAYWRIGHT = "playwright"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="DECLARAS_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: Environment = Environment.LOCAL
    log_level: str = "INFO"
    # NoDecode: se leen como texto separado por comas, no como JSON.
    api_keys: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["dev-key-cambiar"])

    database_url: str = "sqlite+aiosqlite:///./var/declaras.db"

    storage_backend: StorageBackend = StorageBackend.LOCAL
    storage_local_root: Path = Path("./var/documents")
    storage_gcs_bucket: str | None = None

    dian_base_url: str = "https://muisca.dian.gov.co"
    dian_adapter: DianAdapterKind = DianAdapterKind.HTTP
    dian_headless: bool = True
    dian_max_concurrent_sessions: int = Field(default=2, ge=1, le=10)
    dian_nav_timeout_ms: int = Field(default=45_000, ge=5_000)
    dian_max_login_attempts: int = Field(default=2, ge=1, le=2)
    dian_capture_evidence: bool = True
    dian_challenge_ttl_s: int = Field(default=600, ge=60)

    worker_enabled: bool = True
    worker_poll_interval_s: float = Field(default=2.0, gt=0)
    worker_lease_ttl_s: int = Field(default=900, ge=60)
    worker_max_attempts: int = Field(default=3, ge=1)

    @field_validator("api_keys", mode="before")
    @classmethod
    def _parse_api_keys(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.env is Environment.PRODUCTION


@lru_cache
def get_settings() -> Settings:
    return Settings()
