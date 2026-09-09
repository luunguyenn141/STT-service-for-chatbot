from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SUPPORTED_STT_PROVIDERS = frozenset({"elevenlabs", "phowhisper"})


class Settings(BaseSettings):
    """Configuration read from environment variables or a local .env file."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    elevenlabs_api_key: SecretStr | None = None
    stt_provider: str = "elevenlabs"
    stt_model_id: str = "scribe_v2"
    stt_keyterms: str = ""
    phowhisper_model_id: str = "vinai/PhoWhisper-base"
    phowhisper_device: int = -1
    max_upload_mb: int = 25
    request_timeout_seconds: float = 60.0
    service_api_key: SecretStr | None = None
    rate_limit_per_minute: int = 60
    allowed_origins: str = "http://localhost:8000"

    @field_validator("rate_limit_per_minute")
    @classmethod
    def validate_rate_limit(cls, value: int) -> int:
        if value < 0:
            return 0
        return value

    @field_validator("stt_provider")
    @classmethod
    def validate_provider(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in SUPPORTED_STT_PROVIDERS:
            choices = ", ".join(sorted(SUPPORTED_STT_PROVIDERS))
            raise ValueError(f"STT_PROVIDER must be one of: {choices}.")
        return provider

    @field_validator("stt_model_id", "phowhisper_model_id")
    @classmethod
    def validate_model_id(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("A model ID must not be empty.")
        return value.strip()

    @field_validator("phowhisper_device")
    @classmethod
    def validate_phowhisper_device(cls, value: int) -> int:
        if value < -1:
            raise ValueError("PHOWHISPER_DEVICE must be -1 for CPU or a non-negative CUDA device index.")
        return value

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
        if self.stt_provider == "phowhisper":
            return bool(self.phowhisper_model_id)
        return self.elevenlabs_api_key is not None and bool(
            self.elevenlabs_api_key.get_secret_value().strip()
        )

    @property
    def active_model_id(self) -> str:
        if self.stt_provider == "phowhisper":
            return self.phowhisper_model_id
        return self.stt_model_id

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def configured_keyterms(self) -> list[str]:
        return [term.strip() for term in self.stt_keyterms.split(",") if term.strip()]

    @property
    def is_auth_enabled(self) -> bool:
        return self.service_api_key is not None and bool(
            self.service_api_key.get_secret_value().strip()
        )

    @property
    def cors_origins(self) -> list[str]:
        origins = [origin.strip() for origin in self.allowed_origins.split(",") if origin.strip()]
        return origins or ["http://localhost:8000"]


@lru_cache
def get_settings() -> Settings:
    return Settings()