from __future__ import annotations

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class AlarmApiSettings(BaseSettings):
    """Connector configuration (env prefix ``ALARM_API_``)."""

    model_config = SettingsConfigDict(env_prefix="ALARM_API_", env_file=".env", extra="ignore")

    base_url: str = "http://localhost:8000"
    token: SecretStr = SecretStr("demo-token")
    client_id: str = "alarm-mcp-server"
    connect_timeout_seconds: float = Field(default=3.0, gt=0)
    timeout_seconds: float = Field(default=10.0, gt=0, description="Per-attempt read timeout")
    total_timeout_seconds: float = Field(default=30.0, gt=0, description="Deadline across all retries")
    max_retries: int = Field(default=3, ge=0, le=10)
    backoff_base_seconds: float = Field(default=0.25, ge=0)
    backoff_max_seconds: float = Field(default=4.0, ge=0)
    max_pages: int = Field(default=20, ge=1, le=1000, description="Safety cap when following pagination")
