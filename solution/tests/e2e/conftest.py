"""Starts the complete stack as real HTTP services (uvicorn) on ephemeral ports.

simulator  <-HTTP+bearer-  alarm MCP server (Streamable HTTP + bearer)  <-MCP-  copilot backend (+GUI)
                           work-orders MCP server (Streamable HTTP + bearer) <-MCP-+
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import uvicorn
from pydantic import SecretStr

from alarm_connector import AlarmApiClient, AlarmApiSettings
from alarm_mcp import build_server
from alarm_mcp.app import create_http_app
from alarm_mcp.config import McpServerSettings
from alarm_simulator.app import create_app as create_sim
from alarm_simulator.config import SimulatorSettings
from copilot.api import create_app as create_copilot
from copilot.config import CopilotSettings
from platform_common.asgi_auth import BearerAuthMiddleware
from work_orders_mcp import build_server as build_wo

ROOT = Path(__file__).resolve().parents[2]
END = datetime(2026, 10, 12, tzinfo=UTC)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class _Server(threading.Thread):
    def __init__(self, app: object, port: int) -> None:
        super().__init__(daemon=True)
        self.server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error", lifespan="on")
        )

    def run(self) -> None:
        self.server.run()

    def stop(self) -> None:
        self.server.should_exit = True
        self.join(timeout=10)


def _wait(url: str) -> None:
    deadline = time.time() + 30
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=1).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    raise RuntimeError(f"service did not start: {url}")


@dataclass
class Stack:
    sim_url: str
    mcp_url: str
    copilot_url: str
    sim_token: str
    sim_app: object


@pytest.fixture(scope="session")
def http_stack(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Stack]:
    ports = {k: _free_port() for k in ("sim", "mcp", "wo", "copilot")}
    sim_token, mcp_token, wo_token = "e2e-api-token", "e2e-mcp-token", "e2e-wo-token"
    sim_app = create_sim(
        SimulatorSettings(data_end=END, api_tokens=SecretStr(sim_token), log_level="WARNING")
    )
    api = AlarmApiClient(
        AlarmApiSettings(
            base_url=f"http://127.0.0.1:{ports['sim']}", token=SecretStr(sim_token), backoff_base_seconds=0.05
        )
    )
    mcp_app = create_http_app(build_server(api), McpServerSettings(auth_token=SecretStr(mcp_token)))
    wo_app = BearerAuthMiddleware(build_wo("e2e-signing-key").streamable_http_app(host="127.0.0.1"), wo_token)
    settings = CopilotSettings(
        _env_file=None,
        MCP_SERVER_URL=f"http://127.0.0.1:{ports['mcp']}/mcp",
        MCP_AUTH_TOKEN=mcp_token,
        WORK_ORDERS_MCP_URL=f"http://127.0.0.1:{ports['wo']}/mcp",
        WORK_ORDERS_MCP_TOKEN=wo_token,
        RAG_INDEX_PATH=str(tmp_path_factory.mktemp("e2e-index")),
        DOCUMENT_PATH=str(ROOT / "rag" / "documents"),
        COPILOT_REFERENCE_TIME=END.isoformat(),
        COPILOT_FRONTEND_DIR=str(ROOT / "apps" / "frontend"),
        ENABLE_DEMO_CONTROLS=True,
        ALARM_API_BASE_URL=f"http://127.0.0.1:{ports['sim']}",
        ALARM_API_TOKEN=sim_token,
    )
    servers = [_Server(sim_app, ports["sim"]), _Server(mcp_app, ports["mcp"]), _Server(wo_app, ports["wo"])]
    for s in servers:
        s.start()
    _wait(f"http://127.0.0.1:{ports['sim']}/health")
    _wait(f"http://127.0.0.1:{ports['mcp']}/health")
    _wait(f"http://127.0.0.1:{ports['wo']}/health")
    copilot = _Server(create_copilot(settings), ports["copilot"])
    copilot.start()
    servers.append(copilot)
    _wait(f"http://127.0.0.1:{ports['copilot']}/api/health/live")
    yield Stack(
        f"http://127.0.0.1:{ports['sim']}",
        f"http://127.0.0.1:{ports['mcp']}/mcp",
        f"http://127.0.0.1:{ports['copilot']}",
        sim_token,
        sim_app,
    )
    for s in reversed(servers):
        s.stop()
