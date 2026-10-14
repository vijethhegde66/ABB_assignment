"""Start the work-orders MCP server: ``python -m work_orders_mcp``."""

from __future__ import annotations

import uvicorn
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from platform_common import configure_logging
from platform_common.asgi_auth import BearerAuthMiddleware
from work_orders_mcp.server import build_server


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WO_MCP_", env_file=".env", extra="ignore")

    transport: str = "streamable-http"
    host: str = "127.0.0.1"
    port: int = 9100
    signing_key: SecretStr = SecretStr("change-me-local-dev-only")
    auth_token: SecretStr = SecretStr("")
    log_level: str = "INFO"


def main() -> None:
    s = Settings()
    configure_logging("work-orders-mcp-server", s.log_level)
    server = build_server(s.signing_key.get_secret_value())
    if s.transport == "stdio":
        server.run("stdio")
        return

    app = BearerAuthMiddleware(server.streamable_http_app(host=s.host), s.auth_token.get_secret_value())
    uvicorn.run(app, host=s.host, port=s.port, log_level="warning")


if __name__ == "__main__":
    main()
