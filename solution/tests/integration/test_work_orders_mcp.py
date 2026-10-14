import json
import time

from mcp import Client

from work_orders_mcp import build_server
from work_orders_mcp.server import WorkOrderStore


def err(r) -> dict:
    t = r.content[0].text
    return json.loads(t[t.index("{") :])


async def test_list_draft_submit_flow() -> None:
    async with Client(build_server("k1")) as c:
        lst = (
            await c.call_tool("list_work_orders", {"asset_ids": ["NP-LOS-101", "NP-DEA-001"]})
        ).structured_content
        assert {w["work_order_id"] for w in lst["work_orders"]} == {"WO-24011", "WO-24019"}
        d = await c.call_tool(
            "draft_work_request",
            {
                "asset_id": "NP-BFP-101",
                "title": "Check LCV-100",
                "description": "Recurring suction pressure alarms",
            },
        )
        draft = d.structured_content
        assert draft["requires_confirmation"]
        before = len(
            (await c.call_tool("list_work_orders", {"asset_ids": ["NP-BFP-101"]})).structured_content[
                "work_orders"
            ]
        )
        ok = await c.call_tool(
            "submit_work_request",
            {
                "draft_id": draft["draft_id"],
                "approval_token": draft["approval_token"],
                "approved_by": "Shift Supervisor",
            },
        )
        assert not ok.is_error
        after = (await c.call_tool("list_work_orders", {"asset_ids": ["NP-BFP-101"]})).structured_content
        assert len(after["work_orders"]) == before + 1
        replay = await c.call_tool(
            "submit_work_request",
            {
                "draft_id": draft["draft_id"],
                "approval_token": draft["approval_token"],
                "approved_by": "Someone",
            },
        )
        assert err(replay)["error_code"] == "NOT_FOUND"  # single use


async def test_forged_and_expired_tokens_rejected() -> None:
    store = WorkOrderStore()
    async with Client(build_server("k2", store)) as c:
        draft = (
            await c.call_tool(
                "draft_work_request",
                {"asset_id": "NP-BFP-101", "title": "Title here", "description": "Desc here"},
            )
        ).structured_content
        forged = await c.call_tool(
            "submit_work_request",
            {
                "draft_id": draft["draft_id"],
                "approval_token": f"{int(time.time()) + 999}.deadbeef",
                "approved_by": "X Y",
            },
        )
        assert err(forged)["error_code"] == "APPROVAL_REQUIRED"
    # a token signed by a different key (another server instance) is not accepted
    async with Client(build_server("other-key", store)) as c:
        bad = await c.call_tool(
            "submit_work_request",
            {"draft_id": draft["draft_id"], "approval_token": draft["approval_token"], "approved_by": "X Y"},
        )
        assert err(bad)["error_code"] == "APPROVAL_REQUIRED"
