"""Configuration: environment settings (12-factor) and the model seed file.

Bad config fails fast at startup (architecture.md §6). Production refuses development defaults
(SEC-12, SEC-17).
"""

from __future__ import annotations

import base64
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_MIGRATIONS_DIR = BACKEND_DIR / "migrations"
DEFAULT_MODEL_CONFIG = BACKEND_DIR.parent / "config" / "models.yaml"

# Development-only defaults. ``Settings.validate_for_production`` refuses them.
DEV_SESSION_SECRET = "dev-only-change-me"  # noqa: S105
DEV_MASTER_KEY = base64.b64encode(b"codura-dev-only-key-change-me!!!").decode()
DEV_ADMIN_PASSWORD = "admin"  # noqa: S105


class ConfigError(RuntimeError):
    """Raised when configuration is invalid; the process must not start."""


# --- Model seed file (specs/ports.md §7) -------------------------------------------------------

License = Literal["apache-2.0", "mit", "commercial-api"]


class ModelProfileSeed(BaseModel):
    provider: Literal["ollama", "openai_compat", "anthropic", "fake"]
    model: str
    license: License
    base_url: str | None = None
    api_key_env: str | None = None
    temperature: float = 0.1
    max_output_tokens: int = 800
    disable_thinking: bool = False
    price_in_per_mtok_usd: float = 0.0
    price_out_per_mtok_usd: float = 0.0
    extra: dict[str, Any] = Field(default_factory=dict)


class EmbeddingConfig(BaseModel):
    """Fixed for the installation (ADR-0015)."""

    provider: Literal["ollama", "openai_compat", "fake"] = "ollama"
    model: str = "bge-m3"
    dimension: Literal[1024] = 1024
    base_url: str | None = None
    api_key_env: str | None = None


class OcrConfig(BaseModel):
    engine: Literal["tesseract", "fake"] = "tesseract"
    text_layer_min_chars: int = 30
    low_confidence_threshold: float = 60
    page_timeout_s: float = 20
    render_dpi: int = 300


class ModelConfig(BaseModel):
    default_profile: str = "local-default"
    chat_profiles: dict[str, ModelProfileSeed]
    embedding: EmbeddingConfig = Field(default_factory=EmbeddingConfig)
    ocr: OcrConfig = Field(default_factory=OcrConfig)

    @field_validator("chat_profiles")
    @classmethod
    def _non_empty(cls, v: dict[str, ModelProfileSeed]) -> dict[str, ModelProfileSeed]:
        if not v:
            raise ValueError("at least one chat profile is required")
        return v

    def model_post_init(self, __context: Any) -> None:
        if self.default_profile not in self.chat_profiles:
            raise ValueError(f"default_profile {self.default_profile!r} is not a chat profile")


def load_model_config(path: Path) -> ModelConfig:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"model config file not found: {path}") from exc
    try:
        return ModelConfig.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"invalid model config {path}: {exc}") from exc


# --- Environment settings ----------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", protected_namespaces=())

    app_env: Literal["local", "test", "production"] = "local"
    public_base_url: str = "http://localhost:8080"
    log_level: str = "info"

    # Postgres: owner runs migrations/bootstrap; the app connects as a non-owner role (RLS applies).
    database_owner_url: str = "postgresql+psycopg://codura_owner:codura@localhost:5432/codura_docs"
    database_app_url: str = "postgresql+psycopg://codura_app:codura_app@localhost:5432/codura_docs"
    database_worker_url: str = (
        "postgresql+psycopg://codura_worker:codura_worker@localhost:5432/codura_docs"
    )
    db_app_password: SecretStr = SecretStr("codura_app")
    db_worker_password: SecretStr = SecretStr("codura_worker")
    db_platform_password: SecretStr = SecretStr("codura_platform")
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR

    session_secret: SecretStr = SecretStr(DEV_SESSION_SECRET)
    secrets_master_key: SecretStr = SecretStr(DEV_MASTER_KEY)
    secrets_key_version: int = Field(default=1, ge=1, le=255)
    secrets_previous_keys: str = ""  # "1:<base64>,2:<base64>" for rotation

    bootstrap_platform_admin_email: str = "platform@local.test"
    bootstrap_platform_admin_password: SecretStr = SecretStr(DEV_ADMIN_PASSWORD)
    seed_demo_tenant: bool = False

    ollama_base_url: str = "http://localhost:11434"
    model_config_file: Path = DEFAULT_MODEL_CONFIG

    blob_store: Literal["local", "s3", "memory"] = "local"
    blob_local_path: Path = Path("/data/blobs")
    s3_endpoint: str | None = None
    s3_bucket: str | None = None
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    s3_region: str = "us-east-1"

    email_backend: Literal["smtp", "fake"] = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_starttls: bool = False
    mail_from: str = "no-reply@codura.local"

    language_detector: Literal["lingua", "fake"] = "lingua"
    database_platform_url: str = (
        "postgresql+psycopg://codura_platform:codura_platform@localhost:5432/codura_docs"
    )
    cookie_secure: bool | None = None  # default: secure when PUBLIC_BASE_URL is https
    session_ttl_hours: int = 12
    signup_enabled: bool = True

    # Public Chat API abuse limits (SEC-06). Buckets refill continuously; 0 disables a bucket.
    chat_rate_per_key_per_min: int = 60
    chat_rate_per_tenant_per_min: int = 120
    chat_rate_per_visitor_per_min: int = 10
    chat_max_concurrent_per_key: int = 4
    chat_max_concurrent_per_tenant: int = 6
    chat_max_question_chars: int = 2000
    public_rate_per_ip_per_min: int = 120  # config, history, feedback
    hosted_lookup_per_ip_per_min: int = 30
    trust_forwarded_for: bool = True  # Caddy is the only entry point (ADR-0013)

    # Payments (specs/billing.md §8, ADR-0026). `manual` makes no network calls (NFR-01).
    payment_provider: Literal["manual", "stripe"] = "manual"
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_api_base: str = "https://api.stripe.com"
    billing_scheduler_enabled: bool = True  # worker enqueues rollup/cycle Jobs on a schedule

    max_upload_mb: int = 50
    max_pages: int = 500
    worker_concurrency: int = 2
    worker_poll_interval_s: float = 2.0

    def validate_for_production(self) -> None:
        if self.app_env != "production":
            return
        problems: list[str] = []
        if self.session_secret.get_secret_value() == DEV_SESSION_SECRET:
            problems.append("SESSION_SECRET uses the development default")
        if self.secrets_master_key.get_secret_value() == DEV_MASTER_KEY:
            problems.append("SECRETS_MASTER_KEY uses the development default")
        if self.bootstrap_platform_admin_password.get_secret_value() == DEV_ADMIN_PASSWORD:
            problems.append("BOOTSTRAP_PLATFORM_ADMIN_PASSWORD uses the development default")
        for name in ("db_app_password", "db_worker_password", "db_platform_password"):
            secret: SecretStr = getattr(self, name)
            if secret.get_secret_value().startswith("codura_"):
                problems.append(f"{name.upper()} uses the development default")
        if problems:
            raise ConfigError("refusing to start in production: " + "; ".join(problems))

    def load_models(self) -> ModelConfig:
        return load_model_config(self.model_config_file)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def env_secret(name: str | None) -> str | None:
    """Read a provider credential from the environment by variable *name* (seed files only)."""
    return os.environ.get(name) if name else None
