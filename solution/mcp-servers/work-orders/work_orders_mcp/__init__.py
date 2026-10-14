"""Optional secondary MCP server: maintenance work orders (CMMS mock).

Demonstrates (a) a second source system in the same investigation workflow
(open work orders on the asset and related assets) and (b) a *write* tool
protected by an explicit, server-enforced human approval step.
"""

from work_orders_mcp.server import SERVER_NAME, build_server

__all__ = ["SERVER_NAME", "build_server"]
