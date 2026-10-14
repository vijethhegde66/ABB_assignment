"""Contract tests: replay every request of the assignment's Postman collections against the simulator.

The Postman collections (test-data/postman/) are the API specification. Each
request is executed in order with collection-variable substitution; the
variable-setting logic of the Postman test scripts (asset_id from search
results, alarm_id from alarm lists, calculation_id, flood windows) is mirrored
so that chained flows run exactly as in Postman/Newman.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

TOKEN = "test-token"  # matches tests/conftest.py

POSTMAN = Path(__file__).resolve().parents[2] / "test-data" / "postman"
COLLECTIONS = sorted(POSTMAN.glob("*.postman_collection.json"))


def _requests(items: list[dict[str, Any]], prefix: str = "") -> list[tuple[str, dict[str, Any]]]:
    out = []
    for it in items:
        if "item" in it:
            out.extend(_requests(it["item"], f"{prefix}{it['name']} / "))
        else:
            out.append((prefix + it["name"], it))
    return out


def _subst(text: str, variables: dict[str, str]) -> str:
    for _ in range(3):  # nested variables such as window_start -> {{start_time}}
        text = re.sub(r"\{\{(\w+)\}\}", lambda m: variables.get(m.group(1), m.group(0)), text)
    return text


def _apply_test_script(script: str, body: Any, variables: dict[str, str]) -> None:
    """Mirror the collection-variable assignments performed by the Postman test scripts."""
    if not isinstance(body, dict):
        return
    results = body.get("results") or []
    if "'asset_id'" in script and results:
        variables["asset_id"] = results[0]["asset_id"]
        variables["asset_id_2"] = (results[1] if len(results) > 1 else results[0])["asset_id"]
        variables["asset_id_3"] = (results[2] if len(results) > 2 else results[0])["asset_id"]
    if "'alarm_id'" in script and body.get("data"):
        variables["alarm_id"] = body["data"][0]["alarm_id"]
    if "'calculation_id'" in script and body.get("calculation_id"):
        variables["calculation_id"] = body["calculation_id"]
    if "'window_start'" in script:
        windows = body.get("flood_windows") or []
        variables["window_start"] = windows[0]["start"] if windows else variables["start_time"]
        variables["window_end"] = windows[0]["end"] if windows else variables["end_time"]
    if "to.be.above(0)" in script:
        rows = body.get("results") or body.get("data") or []
        assert len(rows) > 0, "Postman assertion: result list must not be empty"


@pytest.mark.parametrize("collection", COLLECTIONS, ids=lambda p: p.stem)
def test_postman_collection_replay(collection: Path, sim_app) -> None:
    spec = json.loads(collection.read_text())
    variables = {v["key"]: v["value"] for v in spec.get("variable", [])}
    variables["auth_token"] = TOKEN
    client = TestClient(sim_app)
    executed = 0
    for name, item in _requests(spec["item"]):
        req = item["request"]
        url = req["url"] if isinstance(req["url"], str) else req["url"]["raw"]
        url = _subst(url, variables).replace(variables["baseUrl"], "")
        headers = {h["key"]: _subst(h["value"], variables) for h in req.get("header", [])}
        if spec.get("auth", {}).get("type") == "bearer":
            headers["Authorization"] = f"Bearer {variables['auth_token']}"
        body = req.get("body", {}).get("raw")
        resp = client.request(
            req["method"], url, headers=headers, content=_subst(body, variables) if body else None
        )
        assert resp.status_code == 200, f"{collection.name} :: {name} -> {resp.status_code} {resp.text[:300]}"
        if "trace_id" in headers:
            assert resp.headers["trace_id"] == headers["trace_id"], "trace header must be propagated"
        script = " ".join(
            " ".join(e["script"]["exec"]) for e in item.get("event", []) if e["listen"] == "test"
        )
        _apply_test_script(script, resp.json(), variables)
        executed += 1
    assert executed >= 15


def test_auth_is_required(sim_app) -> None:
    c = TestClient(sim_app)
    assert c.get("/health").status_code == 200
    r = c.get("/alarms", params={"site": "NorthPlant"})
    assert r.status_code == 401 and r.json()["error"]["code"] == "UNAUTHORIZED"
    assert c.get("/alarms", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_validation_and_not_found_errors(sim_app) -> None:
    c = TestClient(sim_app)
    h = {"Authorization": f"Bearer {TOKEN}", "trace_id": "trace-err"}
    bad = c.post(
        "/alarms/summary",
        headers=h,
        json={"time_range": {"start_time": "2026-07-01T00:00:00Z", "end_time": "2026-05-01T00:00:00Z"}},
    )
    assert bad.status_code == 422 and bad.json()["error"]["trace_id"] == "trace-err"
    extra = c.post("/alarms/priority-score", headers=h, json={"alarm_id": "ALM-0000001", "unexpected": 1})
    assert extra.status_code == 422
    assert c.get("/assets/NP-XXX-000/metadata", headers=h).status_code == 404
    assert c.get("/alarms/ALM-9999999", headers=h).status_code == 404
    assert c.get("/alarms", headers=h, params={"page_size": 10000}).status_code == 422


def test_fault_injection_admin_api(sim_app) -> None:
    c = TestClient(sim_app)
    h = {"Authorization": f"Bearer {TOKEN}"}
    c.post(
        "/_sim/faults",
        headers=h,
        json={"path_prefix": "/alarms/trends", "mode": "error", "status_code": 429, "remaining": 1},
    )
    body = {
        "site": "NorthPlant",
        "time_range": {"start_time": "2026-05-01T00:00:00Z", "end_time": "2026-07-01T00:00:00Z"},
    }
    first = c.post("/alarms/trends", headers=h, json=body)
    assert first.status_code == 429 and first.headers["Retry-After"] == "1"
    assert c.post("/alarms/trends", headers=h, json=body).status_code == 200
    assert c.delete("/_sim/faults", headers=h).json() == {"rules": []}
