from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _today_utc() -> datetime:
    now = datetime.now(UTC)
    return now.replace(minute=0, second=0, microsecond=0)


class SimulatorSettings(BaseSettings):
    """Configuration via environment variables (prefix ``SIM_``)."""

    model_config = SettingsConfigDict(env_prefix="SIM_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"  # noqa: S104 - container service
    port: int = 8000
    # Tokens accepted by the simulator (comma-separated). Mirrors ALARM_API_TOKEN on the client side.
    api_tokens: SecretStr = SecretStr("demo-token")
    seed: int = 42
    data_start: datetime = datetime(2026, 4, 1, tzinfo=UTC)
    # End of the synthetic history. Defaults to "now" so that "last 90 days" questions always have data.
    data_end: datetime | None = None
    # Global fault injection (in addition to the runtime /_sim/faults admin API).
    latency_ms: int = 0
    fault_rate: float = 0.0
    log_level: str = "INFO"

    @field_validator("data_start", "data_end")
    @classmethod
    def _tz(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v

    def resolved_end(self) -> datetime:
        end = self.data_end or _today_utc()
        if end <= self.data_start:
            end = self.data_start + timedelta(days=30)
        return end

    def token_set(self) -> set[str]:
        return {t.strip() for t in self.api_tokens.get_secret_value().split(",") if t.strip()}
