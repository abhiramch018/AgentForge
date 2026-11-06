"""Load LLM settings from environment variables."""

from pydantic import Field, SecretStr, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.exceptions import LLMConfigurationError

SUPPORTED_LLM_PROVIDERS: frozenset[str] = frozenset({"openai"})


class Settings(BaseSettings):
    """Validated settings for the LLM client.

    Environment variable names match the field names in uppercase, for example
    ``LLM_API_KEY``. A local ``.env`` file is read when it exists. Real
    environment variables override that file.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    llm_provider: str
    llm_model: str
    llm_api_key: SecretStr
    llm_timeout_seconds: float = Field(default=30.0, gt=0)
    llm_max_output_tokens: int = Field(default=512, gt=0)

    @field_validator("llm_provider")
    @classmethod
    def provider_must_be_supported(cls, value: str) -> str:
        provider = value.strip().lower()
        if provider not in SUPPORTED_LLM_PROVIDERS:
            supported = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
            raise ValueError(f"must be one of: {supported}")
        return provider

    @field_validator("llm_model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        model = value.strip()
        if not model:
            raise ValueError("must not be blank")
        return model

    @field_validator("llm_api_key")
    @classmethod
    def api_key_must_not_be_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return SecretStr(value.get_secret_value().strip())


def load_settings(*, env_file: str | None = ".env") -> Settings:
    """Read and validate LLM settings.

    ``env_file`` defaults to ``.env`` in the current working directory. Tests
    pass ``env_file=None`` so they do not depend on a local secrets file.
    """

    try:
        if env_file is None:
            return Settings(_env_file=None)
        return Settings(_env_file=env_file)
    except ValidationError as exc:
        raise LLMConfigurationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"])
        parts.append(f"{location}: {error['msg']}")
    return "Invalid LLM configuration. " + "; ".join(parts)
