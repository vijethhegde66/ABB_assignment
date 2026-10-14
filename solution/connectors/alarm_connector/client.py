"""Async HTTP client for the Alarm Management API."""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import BaseModel, Field

from alarm_connector.config import AlarmApiSettings
from alarm_connector.errors import (
    AlarmApiError,
    BadUpstreamResponseError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
    error_for_status,
)
from platform_common import TraceContext, current_trace, get_logger, new_id, redact
from platform_common.tracing import CLIENT_ID_HEADER, METADATA_TAG_HEADER, REQUEST_ID_HEADER, TRACE_HEADER

log = get_logger("alarm_connector")

RETRYABLE_STATUS = {429, 502, 503, 504}


class CallRecord(BaseModel):
    """Observability record for one logical API call (including its retries)."""

    method: str
    path: str
    params: dict[str, Any] | None = None
    body: dict[str, Any] | None = None
    status_code: int | None = None
    attempts: int = 0
    retries: list[str] = Field(default_factory=list, description="Reason for each retry")
    duration_ms: float = 0.0
    outcome: str = "pending"  # success | error
    error_code: str | None = None
    trace_id: str | None = None
    request_id: str | None = None


class ApiResponse(BaseModel):
    data: dict[str, Any]
    call: CallRecord


class AlarmApiClient:
    def __init__(
        self,
        settings: AlarmApiSettings | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.settings = settings or AlarmApiSettings()
        self._sleep = sleep
        timeout = httpx.Timeout(self.settings.timeout_seconds, connect=self.settings.connect_timeout_seconds)
        self._http = httpx.AsyncClient(base_url=self.settings.base_url, timeout=timeout, transport=transport)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> AlarmApiClient:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    # ------------------------------------------------------------------ core
    def _headers(self, trace: TraceContext, tag: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.settings.token.get_secret_value()}",
            "Accept": "application/json",
            TRACE_HEADER: trace.trace_id,
            REQUEST_ID_HEADER: trace.request_id,
            CLIENT_ID_HEADER: self.settings.client_id,
        }
        if tag:
            headers[METADATA_TAG_HEADER] = tag
        return headers

    def _backoff(self, attempt: int, retry_after: str | None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self.settings.backoff_max_seconds)
            except ValueError:
                pass
        base = self.settings.backoff_base_seconds * (2 ** (attempt - 1))
        return min(base + random.uniform(0, base / 2), self.settings.backoff_max_seconds)

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        tag: str | None = None,
        trace: TraceContext | None = None,
    ) -> ApiResponse:
        parent = trace or current_trace() or TraceContext(trace_id=new_id("trace"))
        call_trace = parent.child()
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}
        record = CallRecord(
            method=method,
            path=path,
            params=clean_params or None,
            body=redact(dict(json)) if json is not None else None,
            trace_id=call_trace.trace_id,
            request_id=call_trace.request_id,
        )
        started = time.perf_counter()
        deadline = started + self.settings.total_timeout_seconds
        max_attempts = self.settings.max_retries + 1
        last_error: AlarmApiError | None = None

        for attempt in range(1, max_attempts + 1):
            record.attempts = attempt
            retry_after: str | None = None
            try:
                resp = await self._http.request(
                    method,
                    path,
                    params=clean_params or None,
                    json=json,
                    headers=self._headers(call_trace, tag),
                )
            except httpx.TimeoutException:
                last_error = UpstreamTimeoutError(
                    f"Alarm API did not respond within {self.settings.timeout_seconds}s",
                    attempts=attempt,
                    trace_id=call_trace.trace_id,
                )
                record.retries.append("timeout")
            except httpx.TransportError as exc:
                last_error = UpstreamUnavailableError(
                    f"Alarm API unreachable: {type(exc).__name__}",
                    attempts=attempt,
                    trace_id=call_trace.trace_id,
                )
                record.retries.append("connection_error")
            else:
                record.status_code = resp.status_code
                if resp.status_code < 400:
                    try:
                        data = resp.json()
                    except ValueError as exc:
                        raise self._fail(
                            record,
                            started,
                            BadUpstreamResponseError(
                                "Alarm API returned a non-JSON response",
                                status_code=resp.status_code,
                                attempts=attempt,
                                trace_id=call_trace.trace_id,
                            ),
                        ) from exc
                    if not isinstance(data, dict):
                        raise self._fail(
                            record,
                            started,
                            BadUpstreamResponseError(
                                "Alarm API returned an unexpected payload shape",
                                status_code=resp.status_code,
                                attempts=attempt,
                                trace_id=call_trace.trace_id,
                            ),
                        )
                    record.outcome = "success"
                    record.duration_ms = round((time.perf_counter() - started) * 1000, 1)
                    log.info(
                        "alarm_api_call",
                        extra={
                            "tool_api": f"{method} {path}",
                            "status": resp.status_code,
                            "attempts": attempt,
                            "duration_ms": record.duration_ms,
                        },
                    )
                    return ApiResponse(data=data, call=record)
                last_error = self._map_error(resp, attempt, call_trace.trace_id)
                if resp.status_code not in RETRYABLE_STATUS:
                    raise self._fail(record, started, last_error)
                record.retries.append(f"http_{resp.status_code}")
                retry_after = resp.headers.get("Retry-After")

            if attempt >= max_attempts:
                break
            delay = self._backoff(attempt, retry_after)
            if time.perf_counter() + delay >= deadline:
                record.retries.append("deadline_exceeded")
                break
            log.warning(
                "alarm_api_retry",
                extra={
                    "tool_api": f"{method} {path}",
                    "attempt": attempt,
                    "reason": record.retries[-1],
                    "delay_s": round(delay, 2),
                },
            )
            await self._sleep(delay)

        assert last_error is not None
        last_error.attempts = record.attempts
        raise self._fail(record, started, last_error)

    @staticmethod
    def _map_error(resp: httpx.Response, attempt: int, trace_id: str) -> AlarmApiError:
        message = f"Alarm API returned HTTP {resp.status_code}"
        details = None
        try:
            payload = resp.json()
            err = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = err.get("message") or message
            details = err.get("details")
        except ValueError:
            pass
        cls = error_for_status(resp.status_code)
        return cls(
            message, status_code=resp.status_code, attempts=attempt, trace_id=trace_id, details=details
        )

    @staticmethod
    def _fail(record: CallRecord, started: float, error: AlarmApiError) -> AlarmApiError:
        record.outcome = "error"
        record.error_code = error.code
        record.duration_ms = round((time.perf_counter() - started) * 1000, 1)
        error.call_record = record  # type: ignore[attr-defined]
        log.warning(
            "alarm_api_error",
            extra={
                "tool_api": f"{record.method} {record.path}",
                "error_code": error.code,
                "status": record.status_code,
                "attempts": record.attempts,
            },
        )
        return error

    # ------------------------------------------------------------------ typed operations
    async def health(self) -> ApiResponse:
        return await self.request("GET", "/health")

    async def search_assets(
        self,
        query: str,
        *,
        site: str | None = None,
        unit: str | None = None,
        asset_type: str | None = None,
        limit: int = 10,
    ) -> ApiResponse:
        return await self.request(
            "GET",
            "/assets/search",
            tag="asset-search",
            params={"query": query, "site": site, "unit": unit, "asset_type": asset_type, "limit": limit},
        )

    async def get_asset_metadata(self, asset_id: str) -> ApiResponse:
        return await self.request("GET", f"/assets/{asset_id}/metadata", tag="asset-metadata")

    async def list_alarms(self, **params: Any) -> ApiResponse:
        return await self.request("GET", "/alarms", params=params, tag="alarm-retrieval")

    async def list_alarms_all_pages(
        self, max_pages: int | None = None, **params: Any
    ) -> tuple[list[dict[str, Any]], dict[str, Any], list[CallRecord]]:
        """Follow pagination until exhausted or ``max_pages`` reached."""
        cap = min(max_pages or self.settings.max_pages, self.settings.max_pages)
        page = int(params.pop("page", 1))
        rows: list[dict[str, Any]] = []
        calls: list[CallRecord] = []
        pagination: dict[str, Any] = {}
        for _ in range(cap):
            resp = await self.list_alarms(page=page, **params)
            calls.append(resp.call)
            rows.extend(resp.data.get("data", []))
            pagination = resp.data.get("pagination", {})
            if not pagination.get("has_next"):
                break
            page += 1
        pagination = {
            **pagination,
            "pages_fetched": len(calls),
            "truncated": bool(pagination.get("has_next")),
        }
        return rows, pagination, calls

    async def get_alarm(self, alarm_id: str) -> ApiResponse:
        return await self.request("GET", f"/alarms/{alarm_id}", tag="alarm-detail")

    async def alarm_summary(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/alarms/summary", json=body, tag="alarm-summary")

    async def alarm_trends(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/alarms/trends", json=body, tag="alarm-trends")

    async def alarm_correlation(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/alarms/correlation", json=body, tag="alarm-correlation")

    async def flood_analysis(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/alarms/flood-analysis", json=body, tag="flood-analysis")

    async def rationalization_candidates(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request(
            "POST", "/alarms/rationalization-candidates", json=body, tag="rationalization"
        )

    async def priority_score(self, alarm_id: str) -> ApiResponse:
        return await self.request(
            "POST", "/alarms/priority-score", json={"alarm_id": alarm_id}, tag="priority-score"
        )

    async def operator_actions(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request(
            "POST", "/recommendations/operator-actions", json=body, tag="recommendations"
        )

    async def generate_calculation(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/calculation-code/generate", json=body, tag="kpi-generate")

    async def execute_calculation(self, body: Mapping[str, Any]) -> ApiResponse:
        return await self.request("POST", "/calculation-code/execute", json=body, tag="kpi-execute")

    async def kpi_definitions(self) -> ApiResponse:
        return await self.request("GET", "/analytics/kpi-definitions", tag="kpi-definitions")
