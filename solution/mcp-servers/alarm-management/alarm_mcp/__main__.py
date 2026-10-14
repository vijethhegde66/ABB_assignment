"""Start the Alarm Management MCP server.

python -m alarm_mcp                         # Streamable HTTP on MCP_HOST:MCP_PORT/mcp
MCP_TRANSPORT=stdio python -m alarm_mcp     # stdio (e.g. for Claude Desktop / MCP Inspector)
"""

from __future__ import annotations

import uvicorn

from alarm_connector import AlarmApiClient, AlarmApiSettings
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpServerSettings
from alarm_mcp.server import build_server
from platform_common import configure_logging


def main() -> None:
    settings = McpServerSettings()
    configure_logging("alarm-mcp-server", settings.log_level)
    api = AlarmApiClient(AlarmApiSettings())
    if settings.transport == "stdio":
        build_server(api, close_client_on_shutdown=True).run("stdio")
        return
    app = create_http_app(build_server(api, close_client_on_shutdown=True), settings)
    uvicorn.run(app, host=settings.host, port=settings.port, log_level="warning")


if __name__ == "__main__":
    main()
