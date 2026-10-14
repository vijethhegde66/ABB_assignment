"""Work-order MCP server with a two-phase (draft -> approve) write flow."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from platform_common import get_logger

log = get_logger("work_orders_mcp")
SERVER_NAME = "work-orders"
APPROVAL_TTL_SECONDS = 900

AssetIdParam = Annotated[str, Field(pattern=r"^[A-Z0-9][A-Z0-9-]{2,39}$")]


class WorkOrder(BaseModel):
    work_order_id: str
    asset_id: str
    title: str
    description: str
    priority: Literal["P1", "P2", "P3", "P4"]
    status: Literal["open", "in_progress", "completed", "draft"]
    created_at: str
    due_date: str | None = None
    requested_by: str | None = None


class WorkOrderList(BaseModel):
    asset_ids: list[str]
    open_count: int
    work_orders: list[WorkOrder]


class DraftWorkRequest(BaseModel):
    draft_id: str
    draft: dict[str, Any]
    requires_confirmation: bool = True
    approval_token: str = Field(description="Opaque token; must be echoed back with explicit user approval")
    expires_at: str
    message: str


class SubmittedWorkRequest(BaseModel):
    work_order: WorkOrder
    approved_by: str


SEED_WORK_ORDERS = [
    (
        "WO-24011",
        "NP-LOS-101",
        "Replace BFP 101 lube oil filter element (overdue)",
        "Filter dP trending high; element last changed 14 months ago.",
        "P2",
        "open",
        "2026-08-30",
    ),
    (
        "WO-24019",
        "NP-DEA-001",
        "Calibrate deaerator level control valve LCV-100",
        "Operators report sluggish level recovery after load changes.",
        "P3",
        "open",
        "2026-09-15",
    ),
    (
        "WO-24102",
        "NP-BFP-101",
        "Vibration survey BFP 101 (cavitation suspected)",
        "Raised after repeated suction pressure low alarms.",
        "P2",
        "in_progress",
        "2026-10-20",
    ),
    (
        "WO-23877",
        "NP-BFP-102",
        "BFP 102 bearing inspection",
        "Scheduled 4000 h inspection.",
        "P3",
        "open",
        "2026-11-01",
    ),
    (
        "WO-24150",
        "SP-HX-201",
        "Clean intercooler E-201 tube bundle",
        "Outlet temperature approach degraded.",
        "P2",
        "open",
        "2026-10-05",
    ),
    (
        "WO-24161",
        "SP-ASV-202",
        "Stroke test anti-surge valve FV-202",
        "Position deviation alarms.",
        "P2",
        "open",
        "2026-10-10",
    ),
    (
        "WO-24170",
        "ER-MTR-501",
        "Thermographic survey of M-501 terminal box",
        "Winding temperature alarms.",
        "P2",
        "open",
        "2026-10-18",
    ),
]


class WorkOrderStore:
    def __init__(self) -> None:
        self.orders: dict[str, WorkOrder] = {}
        for wo_id, asset, title, desc, prio, status, due in SEED_WORK_ORDERS:
            self.orders[wo_id] = WorkOrder.model_validate(
                {
                    "work_order_id": wo_id,
                    "asset_id": asset,
                    "title": title,
                    "description": desc,
                    "priority": prio,
                    "status": status,
                    "created_at": "2026-07-01T08:00:00Z",
                    "due_date": due,
                }
            )
        self.drafts: dict[str, dict[str, Any]] = {}
        self.used_drafts: set[str] = set()


def _sign(key: bytes, draft_id: str, payload: dict[str, Any], expires: int) -> str:
    msg = f"{draft_id}|{expires}|{json.dumps(payload, sort_keys=True)}".encode()
    return f"{expires}.{hmac.new(key, msg, hashlib.sha256).hexdigest()}"


def build_server(signing_key: str, store: WorkOrderStore | None = None) -> MCPServer:
    if not signing_key:
        raise ValueError("signing_key is required")
    key = signing_key.encode()
    db = store or WorkOrderStore()
    mcp = MCPServer(
        SERVER_NAME,
        version="1.0.0",
        instructions="Maintenance work orders. Creating a work request requires draft_work_request followed by "
        "submit_work_request with the approval token AFTER explicit user confirmation.",
    )

    @mcp.tool(
        title="List work orders",
        description="List open / in-progress maintenance work orders for one or more assets.",
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    async def list_work_orders(
        asset_ids: Annotated[list[AssetIdParam], Field(min_length=1, max_length=20)],
        include_completed: bool = False,
    ) -> WorkOrderList:
        wanted = set(asset_ids)
        rows = [
            w
            for w in db.orders.values()
            if w.asset_id in wanted and (include_completed or w.status in {"open", "in_progress"})
        ]
        rows.sort(key=lambda w: (w.priority, w.work_order_id))
        return WorkOrderList(
            asset_ids=asset_ids, open_count=sum(1 for w in rows if w.status != "completed"), work_orders=rows
        )

    @mcp.tool(
        title="Draft work request",
        description=(
            "Prepare (but do NOT create) a maintenance work request. Returns a draft and an approval token. "
            "Nothing is written until submit_work_request is called with the token after the user confirms."
        ),
        annotations=ToolAnnotations(read_only_hint=True),
    )
    async def draft_work_request(
        asset_id: AssetIdParam,
        title: Annotated[str, Field(min_length=5, max_length=120)],
        description: Annotated[str, Field(min_length=5, max_length=2000)],
        priority: Literal["P1", "P2", "P3", "P4"] = "P3",
    ) -> DraftWorkRequest:
        draft_id = f"DRAFT-{uuid.uuid4().hex[:10]}"
        payload = {"asset_id": asset_id, "title": title, "description": description, "priority": priority}
        expires = int(time.time()) + APPROVAL_TTL_SECONDS
        db.drafts[draft_id] = payload
        return DraftWorkRequest(
            draft_id=draft_id,
            draft=payload,
            approval_token=_sign(key, draft_id, payload, expires),
            expires_at=datetime.fromtimestamp(expires, UTC).isoformat(),
            message="Draft created. Ask the user to confirm before calling submit_work_request.",
        )

    @mcp.tool(
        title="Submit work request",
        description=(
            "WRITE OPERATION. Create the work request from a draft. Requires the approval_token from "
            "draft_work_request and the identity of the human approver. Never call without explicit user approval."
        ),
        annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=False),
    )
    async def submit_work_request(
        draft_id: Annotated[str, Field(pattern=r"^DRAFT-[a-f0-9]{10}$")],
        approval_token: Annotated[str, Field(min_length=10, max_length=200)],
        approved_by: Annotated[str, Field(min_length=2, max_length=80)],
    ) -> SubmittedWorkRequest:
        payload = db.drafts.get(draft_id)
        if payload is None or draft_id in db.used_drafts:
            raise ToolError(
                json.dumps({"error_code": "NOT_FOUND", "message": "unknown or already used draft"})
            )
        try:
            expires = int(approval_token.split(".", 1)[0])
        except ValueError as exc:
            raise ToolError(
                json.dumps({"error_code": "APPROVAL_REQUIRED", "message": "malformed token"})
            ) from exc
        expected = _sign(key, draft_id, payload, expires)
        if not hmac.compare_digest(expected, approval_token):
            raise ToolError(
                json.dumps({"error_code": "APPROVAL_REQUIRED", "message": "invalid approval token"})
            )
        if expires < time.time():
            raise ToolError(
                json.dumps({"error_code": "APPROVAL_EXPIRED", "message": "approval token expired"})
            )
        db.used_drafts.add(draft_id)
        wo_id = f"WO-{25000 + len(db.orders)}"
        wo = WorkOrder(
            work_order_id=wo_id,
            status="open",
            created_at=datetime.now(UTC).isoformat(),
            requested_by=approved_by,
            **payload,
        )
        db.orders[wo_id] = wo
        log.info("work_request_created", extra={"work_order_id": wo_id, "approved_by": approved_by})
        return SubmittedWorkRequest(work_order=wo, approved_by=approved_by)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok", "server": SERVER_NAME, "work_orders": len(db.orders)})

    return mcp
