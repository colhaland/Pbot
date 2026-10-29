from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    app_env: str = "development"
    app_name: str = "Pbot"
    database_url: str = "sqlite+aiosqlite:///./pbot.db"
    redis_url: str = "redis://localhost:6379/0"
    encryption_master_key: SecretStr | None = None
    encryption_key_version: int = 1
    session_secret: SecretStr = SecretStr("local-only-change-before-deploy")
    session_cookie_name: str = "pbot_session"
    session_idle_minutes: int = 720
    session_absolute_hours: int = 24
    session_cookie_secure: bool = False
    website_limit: int = Field(default=10, ge=1, le=100)
    job_default_timeout_seconds: int = 900
    approval_ttl_seconds: int = 1800
    artifact_dir: str = "./artifacts"
    artifact_signed_url_ttl: int = Field(default=900, ge=60, le=3600)
    browser_max_concurrency: int = Field(default=3, ge=1, le=10)
    telegram_webhook_secret: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    gemini_api_key: SecretStr | None = None
    bootstrap_owner_email: str | None = None
    bootstrap_owner_password: SecretStr | None = None

    def validate_production_secrets(self) -> None:
        if self.app_env == "production":
            if not self.encryption_master_key or not self.session_secret.get_secret_value():
                raise ValueError("ENCRYPTION_MASTER_KEY dan SESSION_SECRET wajib disetel.")
            if self.session_secret.get_secret_value() == "local-only-change-before-deploy":
                raise ValueError("SESSION_SECRET default dilarang pada production.")
            if not self.session_cookie_secure:
                raise ValueError("SESSION_COOKIE_SECURE harus aktif pada production.")


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.validate_production_secrets()
    return settings