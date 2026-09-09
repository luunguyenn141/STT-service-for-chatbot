from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    elevenlabs_api_key: SecretStr | None = None
    stt_provider: str = "elevenlabs"
    stt_model_id: str = "scribe_v2"
    stt_keyterms: str = ""
    max_upload_mb: int = 25
    request_timeout_seconds: float = 60.0
    allowed_origins: str = "http://localhost:8000"

    @field_validator("stt_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        if value.lower() != "elevenlabs":
            raise ValueError("Only the elevenlabs provider is available in this POC.")
        return value.lower()

    @field_validator("max_upload_mb")
    @classmethod
    def validate_upload_limit(cls, value: int) -> int:
        if value < 1 or value > 100:
            raise ValueError("MAX_UPLOAD_MB must be between 1 and 100.")
        return value

    @field_validator("request_timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if value <= 0 or value > 300:
            raise ValueError("REQUEST_TIMEOUT_SECONDS must be between 0 and 300.")
        return value

    @property
    def configured(self) -> bool:
        return self.elevenlabs_api_key is not None and bool(
            self.elevenlabs_api_key.get_secret_value().strip()
        )

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def configured_keyterms(self) -> list[str]:
        return [term.strip() for term in self.stt_keyterms.split(",") if term.strip()]

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
        if "*" in origins:
            raise ValueError("ALLOWED_ORIGINS must not contain '*'.")
        return origins


@lru_cache
def get_settings() -> Settings:
    return Settings()
