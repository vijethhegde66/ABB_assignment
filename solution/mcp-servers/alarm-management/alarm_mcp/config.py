from __future__ import annotations

from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class McpServerSettings(BaseSettings):
    """MCP server process configuration (env prefix ``MCP_``)."""

    model_config = SettingsConfigDict(env_prefix="MCP_", env_file=".env", extra="ignore")

    transport: Literal["stdio", "streamable-http"] = "streamable-http"
    host: str = "127.0.0.1"
    port: int = 9000
    path: str = "/mcp"
    # Shared secret the copilot must present (Authorization: Bearer ...). Empty disables auth (local stdio use).
    auth_token: SecretStr = SecretStr("")
    log_level: str = "INFO"
