"""Generate docs/mcp-tool-catalog.md from the running MCP server definitions (schemas + real example calls).

python scripts/generate_tool_catalog.py
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from mcp import Client
from pydantic import SecretStr

from alarm_connector import AlarmApiClient, AlarmApiSettings
from alarm_mcp import build_server
from alarm_simulator.app import create_app
from alarm_simulator.config import SimulatorSettings
from work_orders_mcp import build_server as build_wo

OUT = Path(__file__).resolve().parents[1] / "docs" / "mcp-tool-catalog.md"
END = "2026-10-12T00:00:00Z"

SOURCE_OP = {
    "search_assets": "GET /assets/search",
    "get_asset_metadata": "GET /assets/{asset_id}/metadata",
    "get_alarms": "GET /alarms (follows pagination when fetch_all_pages=true)",
    "get_alarm_details": "GET /alarms/{alarm_id}",
    "summarize_alarms": "POST /alarms/summary",
    "get_alarm_trends": "POST /alarms/trends",
    "correlate_alarms": "POST /alarms/correlation",
    "analyze_alarm_floods": "POST /alarms/flood-analysis",
    "find_rationalization_candidates": "POST /alarms/rationalization-candidates",
    "score_alarm_priority": "POST /alarms/priority-score",
    "rank_alarm_priorities": "POST /alarms/priority-score × N (concurrent, partial results)",
    "recommend_operator_actions": "POST /recommendations/operator-actions",
    "calculate_kpi": "POST /calculation-code/generate → POST /calculation-code/execute",
    "list_kpi_definitions": "GET /analytics/kpi-definitions",
    "list_work_orders": "CMMS mock (in-memory work-order store)",
    "draft_work_request": "CMMS mock - creates an unsigned draft + HMAC approval token (no write)",
    "submit_work_request": "CMMS mock - WRITE: creates a work order (requires approval token + explicit user confirmation)",
}

EXAMPLES: dict[str, dict[str, Any]] = {
    "search_assets": {"query": "Boiler Feed Pump 101", "limit": 3},
    "get_asset_metadata": {"asset_id": "NP-BFP-101"},
    "get_alarms": {"asset_id": "NP-BFP-102", "status": "active"},
    "get_alarm_details": {"alarm_id": "ALM-0000010"},
    "summarize_alarms": {"asset_ids": ["NP-BFP-101"], "severity": ["high", "critical"], "end_time": END},
    "get_alarm_trends": {
        "asset_ids": ["NP-BFP-101"],
        "bucket": "weekly",
        "lookback_days": 28,
        "end_time": END,
    },
    "correlate_alarms": {"asset_ids": ["NP-BFP-101"], "end_time": END},
    "analyze_alarm_floods": {"unit": "Unit 2", "end_time": END},
    "find_rationalization_candidates": {"site": "NorthPlant", "unit": "Unit 1", "end_time": END},
    "score_alarm_priority": {"alarm_id": "ALM-0000010"},
    "rank_alarm_priorities": {"alarm_ids": ["ALM-0000010", "ALM-0000011", "ALM-9999999"]},
    "recommend_operator_actions": {"alarm_id": "ALM-0000010", "include_historical_pattern": False},
    "calculate_kpi": {"calculation_type": "alarm_flood_index", "unit": "Unit 2", "end_time": END},
    "list_kpi_definitions": {},
    "list_work_orders": {"asset_ids": ["NP-LOS-101", "NP-DEA-001"]},
    "draft_work_request": {
        "asset_id": "NP-DEA-001",
        "title": "Stroke test LCV-100",
        "description": "Recurring deaerator level dips precede BFP 101 suction alarms",
    },
}


def _trim(obj: Any, depth: int = 0) -> Any:
    if isinstance(obj, list):
        out = [_trim(x, depth + 1) for x in obj[:2]]
        if len(obj) > 2:
            out.append(f"… {len(obj) - 2} more")
        return out
    if isinstance(obj, dict):
        return {k: _trim(v, depth + 1) for k, v in obj.items()}
    return obj


def _params(schema: dict[str, Any]) -> str:
    rows = ["| Parameter | Type / constraints | Required | Default | Description |", "|---|---|---|---|---|"]
    req = set(schema.get("required", []))
    for name, p in schema.get("properties", {}).items():
        variants = p.get("anyOf", [p])
        types = []
        for v in variants:
            t = v.get("type", "")
            if t == "array":
                item = v.get("items", {})
                t = f"array<{item.get('type', '')}{'=' + '|'.join(item['enum']) if 'enum' in item else ''}>"
            if "enum" in v:
                t += " = " + "|".join(map(str, v["enum"]))
            cons = [
                f"{k}={v[k]}"
                for k in (
                    "pattern",
                    "minimum",
                    "maximum",
                    "minLength",
                    "maxLength",
                    "minItems",
                    "maxItems",
                    "format",
                )
                if k in v
            ]
            types.append(t + (f" ({', '.join(cons)})" if cons else ""))
        default = json.dumps(p["default"]) if "default" in p else ""
        rows.append(
            f"| `{name}` | {' or '.join(x for x in types if x)} | {'yes' if name in req else ''} | "
            f"{default} | {p.get('description', '')} |"
        )
    return "\n".join(rows)


async def main() -> None:
    sim = create_app(
        SimulatorSettings(data_end=datetime(2026, 10, 12, tzinfo=UTC), api_tokens=SecretStr("t"))
    )
    api = AlarmApiClient(
        AlarmApiSettings(base_url="http://sim", token=SecretStr("t")), transport=httpx.ASGITransport(app=sim)
    )
    sections = []
    index_rows = ["| Tool | Server | Read-only | Source-system operation |", "|---|---|---|---|"]
    for server_name, server in (
        ("alarm-management", build_server(api)),
        ("work-orders", build_wo("doc-key")),
    ):
        async with Client(server) as c:
            for t in (await c.list_tools()).tools:
                ro = bool(t.annotations and t.annotations.read_only_hint)
                index_rows.append(
                    f"| [`{t.name}`](#{t.name.replace('_', '-')}) | {server_name} | "
                    f"{'yes' if ro else '**no - write, approval required**'} | {SOURCE_OP[t.name]} |"
                )
                example = EXAMPLES.get(t.name)
                ex_md = "_Not executed automatically (write operation). See the approval flow below._"
                if example is not None:
                    r = await c.call_tool(t.name, example)
                    body = (
                        r.structured_content
                        if not r.is_error
                        else {"isError": True, "content": r.content[0].text[:400]}
                    )
                    ex_md = (
                        "**Example invocation** (`tools/call`)\n```json\n"
                        + json.dumps({"name": t.name, "arguments": example}, indent=2)
                        + "\n```\n**Example response** (structured content, lists trimmed)\n```json\n"
                        + json.dumps(_trim(body), indent=2, default=str)[:3500]
                        + "\n```"
                    )
                out_props = ", ".join(f"`{k}`" for k in (t.output_schema or {}).get("properties", {}))
                sections.append(f"""### {t.name}

**Title:** {t.title or t.name} · **Server:** `{server_name}` · **Read-only:** {"yes" if ro else "no (write)"}

**Purpose:** {t.description}

**Underlying source-system operation:** {SOURCE_OP[t.name]}

**Input schema**

{_params(t.input_schema)}

**Output schema:** object with {out_props or "(unstructured)"}. Full JSON Schema:

<details><summary>outputSchema</summary>

```json
{json.dumps(t.output_schema, indent=2)}
```
</details>

{ex_md}
""")
    await api.aclose()
    header = f"""# MCP Tool Catalog

_Generated by `scripts/generate_tool_catalog.py` from the live tool definitions; examples are real calls against
the simulator._

## Common behaviour (all Alarm Management tools)

| Aspect | Behaviour |
|---|---|
| **Transport** | Streamable HTTP at `MCP_SERVER_URL` (default `http://alarm-mcp:9000/mcp`) or stdio (`MCP_TRANSPORT=stdio`). |
| **Client → MCP auth** | `Authorization: Bearer $MCP_AUTH_TOKEN` checked by `BearerAuthMiddleware` (constant-time compare); `/health` is public. |
| **MCP → Alarm API auth** | The MCP server holds `ALARM_API_TOKEN` (SecretStr) and sends `Authorization: Bearer …`. Tokens never appear in tool results or logs (redaction filter). |
| **Validation** | Pydantic-typed parameters → JSON Schema `inputSchema`; SDK validates before execution. Cross-field rules (e.g. start < end, at least one scope filter) raise `INVALID_ARGUMENT`. The copilot additionally validates arguments client-side against the discovered schema. |
| **Output validation** | Every tool returns a Pydantic model → advertised `outputSchema`, validated before sending (`structuredContent`). |
| **Trace propagation** | Copilot sends `_meta.trace_id/request_id/conversation_id`; the server forwards `trace_id`, `x-request-id`, `x-client-id: alarm-mcp-server`, `x-metadata-tag: <operation>` to the API and returns `trace.api_calls[]` (method, path, params, status, attempts, retries, duration). |
| **Timeouts** | Per attempt `ALARM_API_TIMEOUT_SECONDS` (10 s), connect 3 s, overall deadline `ALARM_API_TOTAL_TIMEOUT_SECONDS` (30 s). Copilot-side per-call timeout `MCP_TOOL_TIMEOUT_SECONDS` (45 s). |
| **Retries** | Up to `ALARM_API_MAX_RETRIES` (3) on timeouts, connection errors, HTTP 429/502/503/504; exponential backoff with jitter, honours `Retry-After`; 4xx are never retried. |
| **Errors** | `isError: true` with JSON payload `{{error_code, message, retryable, status_code, attempts, trace_id, tool, api_calls}}`. Codes: `INVALID_ARGUMENT` (400/422 or validation), `NOT_FOUND` (404), `AUTHENTICATION_FAILED` (401/403), `RATE_LIMITED` (429 after retries), `UPSTREAM_UNAVAILABLE` (5xx/connection after retries), `UPSTREAM_TIMEOUT`, `BAD_UPSTREAM_RESPONSE` (non-JSON/unexpected shape). |
| **Pagination** | `get_alarms` exposes `page/page_size` and `fetch_all_pages` + `max_pages` (hard cap 20) with `pagination.truncated`. |
| **Annotations** | `readOnlyHint=true, idempotentHint=true, destructiveHint=false` for all alarm tools. |

## Index

{chr(10).join(index_rows)}

## Write-operation approval flow (work-orders server)

1. Copilot proposes a work request (`proposed_actions`) - nothing is written.
2. User clicks **Review & create** → backend calls `draft_work_request` → returns draft + HMAC-signed, single-use,
   15-minute `approval_token`.
3. GUI shows the draft in a confirmation dialog; the user enters their name and confirms.
4. Backend calls `submit_work_request` with `approved=True` (the MCP client refuses non-read-only tools otherwise:
   `APPROVAL_REQUIRED`), the server verifies the token signature, expiry and single use.

## Tools

"""
    OUT.write_text(header + "\n".join(sections), encoding="utf-8")
    print(f"wrote {OUT}")


if __name__ == "__main__":
    asyncio.run(main())
