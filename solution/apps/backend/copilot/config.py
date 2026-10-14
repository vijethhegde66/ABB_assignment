from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class CopilotSettings(BaseSettings):
    """Copilot backend configuration (environment variables, see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    host: str = Field(default="127.0.0.1", alias="COPILOT_HOST")
    port: int = Field(default=8080, alias="COPILOT_PORT")
    log_level: str = Field(default="INFO", alias="COPILOT_LOG_LEVEL")
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000", alias="COPILOT_CORS_ORIGINS"
    )
    serve_frontend: bool = Field(default=True, alias="COPILOT_SERVE_FRONTEND")
    frontend_dir: str = Field(default="apps/frontend", alias="COPILOT_FRONTEND_DIR")

    # --- MCP servers (the ONLY path to the Alarm Management API) ---
    mcp_server_url: str = Field(default="http://127.0.0.1:9000/mcp", alias="MCP_SERVER_URL")
    mcp_auth_token: SecretStr = Field(default=SecretStr(""), alias="MCP_AUTH_TOKEN")
    work_orders_mcp_url: str = Field(default="", alias="WORK_ORDERS_MCP_URL")
    work_orders_mcp_token: SecretStr = Field(default=SecretStr(""), alias="WORK_ORDERS_MCP_TOKEN")
    mcp_tool_timeout_seconds: float = Field(default=45.0, alias="MCP_TOOL_TIMEOUT_SECONDS")
    mcp_connect_timeout_seconds: float = Field(default=10.0, alias="MCP_CONNECT_TIMEOUT_SECONDS")

    # --- RAG ---
    rag_index_path: str = Field(default="var/rag-index", alias="RAG_INDEX_PATH")
    document_path: str = Field(default="rag/documents", alias="DOCUMENT_PATH")
    embedding_provider: str = Field(default="hashing", alias="EMBEDDING_PROVIDER")
    rag_auto_ingest: bool = Field(default=True, alias="RAG_AUTO_INGEST")

    # --- LLM (optional; deterministic grounded composer is used when disabled or on failure) ---
    llm_provider: Literal["none", "anthropic"] = Field(default="none", alias="LLM_PROVIDER")
    llm_model: str = Field(default="claude-opus-5-5", alias="LLM_MODEL")
    llm_api_key: SecretStr = Field(default=SecretStr(""), alias="LLM_API_KEY")
    llm_timeout_seconds: float = Field(default=60.0, alias="LLM_TIMEOUT_SECONDS")
    llm_effort: Literal["low", "medium", "high"] = Field(default="medium", alias="LLM_EFFORT")

    # --- behaviour ---
    default_lookback_days: int = Field(default=90, alias="COPILOT_DEFAULT_LOOKBACK_DAYS")
    reference_time: datetime | None = Field(default=None, alias="COPILOT_REFERENCE_TIME")

    # --- demo / test harness controls (fault injection on the simulator). Disabled by default. ---
    enable_demo_controls: bool = Field(default=False, alias="ENABLE_DEMO_CONTROLS")
    alarm_api_base_url: str = Field(default="http://127.0.0.1:8000", alias="ALARM_API_BASE_URL")
    alarm_api_token: SecretStr = Field(default=SecretStr("demo-token"), alias="ALARM_API_TOKEN")

    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]
