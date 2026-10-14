from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from starlette.types import ASGIApp

from alarm_mcp.config import McpServerSettings
from platform_common.asgi_auth import BearerAuthMiddleware


def create_http_app(server: MCPServer, settings: McpServerSettings) -> ASGIApp:
    """Streamable HTTP ASGI app wrapped with the bearer-token boundary."""
    app = server.streamable_http_app(streamable_http_path=settings.path, host=settings.host)
    return BearerAuthMiddleware(app, settings.auth_token.get_secret_value())
