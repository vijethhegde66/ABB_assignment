"""API client tests: payload/headers, retries, timeouts, error mapping, pagination (HTTP mocked with respx)."""

import httpx
import pytest
import respx
from pydantic import SecretStr

from alarm_connector import (
    AlarmApiClient,
    AlarmApiSettings,
    AuthenticationError,
    BadUpstreamResponseError,
    InvalidArgumentError,
    NotFoundError,
    RateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)
from platform_common import TraceContext

BASE = "http://alarm-api.test"
sleeps: list[float] = []


async def fake_sleep(s: float) -> None:
    sleeps.append(s)


def client(**kw: object) -> AlarmApiClient:
    sleeps.clear()
    params: dict[str, object] = {"backoff_base_seconds": 0.1, **kw}
    settings = AlarmApiSettings(base_url=BASE, token=SecretStr("s3cret"), **params)  # type: ignore[arg-type]
    return AlarmApiClient(settings, sleep=fake_sleep)


@respx.mock
async def test_auth_trace_headers_and_query_params() -> None:
    route = respx.get(f"{BASE}/assets/search").mock(return_value=httpx.Response(200, json={"results": []}))
    c = client()
    resp = await c.request(
        "GET",
        "/assets/search",
        params={"query": "BFP 101", "site": None, "limit": 5},
        tag="asset-search",
        trace=TraceContext(trace_id="trace-xyz"),
    )
    req = route.calls.last.request
    assert req.headers["authorization"] == "Bearer s3cret"
    assert req.headers["trace_id"] == "trace-xyz"
    assert req.headers["x-client-id"] == "alarm-mcp-server"
    assert req.headers["x-metadata-tag"] == "asset-search"
    assert req.headers["x-request-id"].startswith("req-")
    assert "site" not in req.url.params and req.url.params["limit"] == "5"
    assert resp.call.outcome == "success" and resp.call.attempts == 1 and resp.call.trace_id == "trace-xyz"
    await c.aclose()


@respx.mock
async def test_post_body_and_secret_redaction_in_call_record() -> None:
    route = respx.post(f"{BASE}/alarms/summary").mock(return_value=httpx.Response(200, json={"ok": True}))
    c = client()
    resp = await c.alarm_summary({"asset_ids": ["A"], "api_key": "should-not-leak"})
    assert route.calls.last.request.read() == b'{"asset_ids":["A"],"api_key":"should-not-leak"}'
    assert resp.call.body == {"asset_ids": ["A"], "api_key": "***REDACTED***"}
    await c.aclose()


@respx.mock
async def test_retries_transient_503_then_succeeds() -> None:
    route = respx.post(f"{BASE}/alarms/correlation").mock(
        side_effect=[
            httpx.Response(503, json={"error": {"message": "busy"}}),
            httpx.Response(502),
            httpx.Response(200, json={"x": 1}),
        ]
    )
    c = client()
    resp = await c.alarm_correlation({"asset_ids": ["A"]})
    assert route.call_count == 3
    assert resp.call.attempts == 3 and resp.call.retries == ["http_503", "http_502"]
    assert len(sleeps) == 2 and sleeps[1] >= sleeps[0]  # exponential backoff
    await c.aclose()


@respx.mock
async def test_retry_after_header_is_honoured_for_429() -> None:
    respx.get(f"{BASE}/alarms").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}),
            httpx.Response(200, json={"data": []}),
        ]
    )
    c = client(backoff_max_seconds=5)
    await c.list_alarms(asset_id="A")
    assert sleeps == [2.0]
    await c.aclose()


@respx.mock
async def test_retries_exhausted_raises_retryable_error() -> None:
    respx.get(f"{BASE}/alarms").mock(return_value=httpx.Response(503))
    c = client(max_retries=2)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await c.list_alarms(asset_id="A")
    assert exc.value.attempts == 3 and exc.value.retryable
    assert exc.value.call_record.retries == ["http_503", "http_503", "http_503"]
    await c.aclose()


@respx.mock
async def test_429_exhausted_maps_to_rate_limited() -> None:
    respx.get(f"{BASE}/alarms").mock(return_value=httpx.Response(429))
    with pytest.raises(RateLimitedError):
        await client(max_retries=1).list_alarms(asset_id="A")


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, InvalidArgumentError),
        (422, InvalidArgumentError),
        (401, AuthenticationError),
        (403, AuthenticationError),
        (404, NotFoundError),
    ],
)
@respx.mock
async def test_non_retryable_errors_are_mapped_without_retry(status: int, error: type) -> None:
    route = respx.get(f"{BASE}/alarms/ALM-0000001").mock(
        return_value=httpx.Response(
            status, json={"error": {"code": "X", "message": "upstream says no", "details": [{"loc": "a"}]}}
        )
    )
    c = client()
    with pytest.raises(error) as exc:
        await c.get_alarm("ALM-0000001")
    assert route.call_count == 1
    assert exc.value.message == "upstream says no" and exc.value.status_code == status
    assert exc.value.to_dict()["error_code"] == error.code
    await c.aclose()


@respx.mock
async def test_timeout_is_retried_then_mapped() -> None:
    respx.get(f"{BASE}/alarms").mock(side_effect=httpx.ReadTimeout("slow"))
    c = client(max_retries=1)
    with pytest.raises(UpstreamTimeoutError) as exc:
        await c.list_alarms(asset_id="A")
    assert exc.value.attempts == 2
    await c.aclose()


@respx.mock
async def test_connection_error_maps_to_unavailable() -> None:
    respx.get(f"{BASE}/health").mock(side_effect=httpx.ConnectError("refused"))
    with pytest.raises(UpstreamUnavailableError):
        await client(max_retries=0).health()


@respx.mock
async def test_non_json_and_wrong_shape_are_bad_upstream_responses() -> None:
    respx.get(f"{BASE}/health").mock(return_value=httpx.Response(200, text="<html>oops</html>"))
    with pytest.raises(BadUpstreamResponseError):
        await client().health()
    respx.get(f"{BASE}/analytics/kpi-definitions").mock(return_value=httpx.Response(200, json=[1, 2]))
    with pytest.raises(BadUpstreamResponseError):
        await client().kpi_definitions()


@respx.mock
async def test_total_deadline_stops_retries() -> None:
    respx.get(f"{BASE}/alarms").mock(return_value=httpx.Response(503))
    c = client(max_retries=5, total_timeout_seconds=0.05, backoff_base_seconds=1.0)
    with pytest.raises(UpstreamUnavailableError) as exc:
        await c.list_alarms(asset_id="A")
    assert "deadline_exceeded" in exc.value.call_record.retries
    await c.aclose()


@respx.mock
async def test_pagination_follows_pages_and_caps() -> None:
    def page(request: httpx.Request) -> httpx.Response:
        p = int(request.url.params["page"])
        return httpx.Response(
            200,
            json={
                "data": [{"alarm_id": f"ALM-{p}"}],
                "pagination": {"page": p, "has_next": p < 5, "total_pages": 5},
            },
        )

    route = respx.get(f"{BASE}/alarms").mock(side_effect=page)
    c = client()
    rows, pagination, calls = await c.list_alarms_all_pages(max_pages=3, asset_id="A", page_size=1)
    assert [r["alarm_id"] for r in rows] == ["ALM-1", "ALM-2", "ALM-3"]
    assert pagination["pages_fetched"] == 3 and pagination["truncated"] is True and len(calls) == 3
    rows, pagination, _ = await c.list_alarms_all_pages(max_pages=10, asset_id="A")
    assert len(rows) == 5 and pagination["truncated"] is False
    assert route.call_count == 8
    await c.aclose()
