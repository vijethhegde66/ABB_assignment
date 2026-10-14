"""Shared fixtures: a fully in-process stack (simulator -> connector -> MCP servers -> MCP client -> copilot).

In-process wiring uses the real components end to end (FastAPI simulator via
ASGI transport, real MCP servers via the SDK's in-process client), so the
tests exercise the same code paths as the deployed services without ports.
"""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from alarm_connector import AlarmApiClient, AlarmApiSettings
from alarm_mcp import build_server
from alarm_simulator.app import create_app as create_sim_app
from alarm_simulator.config import SimulatorSettings
from copilot.mcp_client import McpServerConfig, McpToolRegistry
from copilot.memory import ConversationStore
from copilot.orchestrator import Orchestrator
from copilot.rag_service import RagService
from work_orders_mcp import build_server as build_wo_server

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "rag" / "documents"
END = datetime(2026, 10, 12, tzinfo=UTC)
TOKEN = "test-token"


async def _no_sleep(_: float) -> None:
    return None


@pytest.fixture
def sim_app() -> FastAPI:
    return create_sim_app(SimulatorSettings(data_end=END, api_tokens=SecretStr(TOKEN), log_level="WARNING"))


class TimeoutASGITransport(httpx.AsyncBaseTransport):
    """ASGI transport that enforces the client's read timeout (httpx.ASGITransport has no network timeouts)."""

    def __init__(self, app: FastAPI, timeout_s: float) -> None:
        self._inner = httpx.ASGITransport(app=app)
        self._timeout = timeout_s

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await asyncio.wait_for(self._inner.handle_async_request(request), self._timeout)
        except TimeoutError as exc:
            raise httpx.ReadTimeout("simulated network timeout", request=request) from exc


def make_api(sim: FastAPI, **overrides: object) -> AlarmApiClient:
    params: dict[str, object] = {
        "base_url": "http://sim",
        "token": SecretStr(TOKEN),
        "backoff_base_seconds": 0.0,
        **overrides,
    }
    settings = AlarmApiSettings(**params)  # type: ignore[arg-type]
    return AlarmApiClient(
        settings, transport=TimeoutASGITransport(sim, settings.timeout_seconds), sleep=_no_sleep
    )


@pytest.fixture
async def api(sim_app: FastAPI) -> AsyncIterator[AlarmApiClient]:
    client = make_api(sim_app)
    yield client
    await client.aclose()


@pytest.fixture
def alarm_server(api: AlarmApiClient):
    return build_server(api)


@pytest.fixture
def wo_server():
    return build_wo_server("test-signing-key")


@pytest.fixture(scope="session")
def rag_index_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("rag-index")


@pytest.fixture(scope="session")
def rag_service(rag_index_dir: Path) -> RagService:
    return RagService(str(rag_index_dir), str(DOCS), "hashing", auto_ingest=True)


@pytest.fixture
def docs_copy(tmp_path: Path) -> Path:
    target = tmp_path / "docs"
    shutil.copytree(DOCS, target)
    return target


@pytest.fixture
def registry(alarm_server, wo_server) -> McpToolRegistry:
    return McpToolRegistry(
        [
            McpServerConfig("alarm-management", in_process=alarm_server),
            McpServerConfig("work-orders", in_process=wo_server, required=False),
        ],
        call_timeout_s=20,
    )


@pytest.fixture
def orchestrator(registry: McpToolRegistry, rag_service: RagService) -> Orchestrator:
    return Orchestrator(registry, rag_service, ConversationStore(), reference_time=END)
