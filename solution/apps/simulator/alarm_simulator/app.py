"""FastAPI application implementing the Alarm Management API contract."""

from __future__ import annotations

import asyncio
import random
import secrets
import time
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response

from alarm_simulator import __version__
from alarm_simulator.catalog import ASSETS
from alarm_simulator.config import SimulatorSettings
from alarm_simulator.models import (
    AssetId,
    CalcExecuteRequest,
    CalcGenerateRequest,
    CorrelationRequest,
    FaultRule,
    FloodRequest,
    PriorityRequest,
    RationalizationRequest,
    RecommendationRequest,
    SummaryRequest,
    TrendsRequest,
)
from alarm_simulator.recommendations import operator_actions
from alarm_simulator.store import KPI_DEFINITIONS, AlarmStore, InvalidRequest, NotFound, Scope
from platform_common import configure_logging, get_logger, new_id
from platform_common.tracing import (
    CLIENT_ID_HEADER,
    METADATA_TAG_HEADER,
    REQUEST_ID_HEADER,
    TRACE_HEADER,
    TRACE_HEADER_ALT,
)

log = get_logger("alarm_simulator")


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


class FaultInjector:
    def __init__(self) -> None:
        self.rules: list[dict[str, Any]] = []

    def match(self, path: str) -> dict[str, Any] | None:
        for rule in self.rules:
            if path.startswith(rule["path_prefix"]):
                if rule.get("remaining") is not None:
                    rule["remaining"] -= 1
                    if rule["remaining"] <= 0:
                        self.rules.remove(rule)
                return rule
        return None


def create_app(settings: SimulatorSettings | None = None) -> FastAPI:
    settings = settings or SimulatorSettings()
    configure_logging("alarm-api-simulator", settings.log_level)
    store = AlarmStore(settings.seed, settings.data_start, settings.resolved_end())
    tokens = settings.token_set()
    faults = FaultInjector()
    request_log: deque[dict[str, Any]] = deque(maxlen=500)
    rng = random.Random(settings.seed)

    app = FastAPI(
        title="Alarm Management API (Simulator)",
        version=__version__,
        description="Simulator implementing the Alarm Management API contract from the assignment Postman collections.",
    )
    app.state.store = store
    app.state.faults = faults
    app.state.request_log = request_log

    # ------------------------------------------------------------------ middleware
    @app.middleware("http")
    async def trace_and_faults(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        trace_id = (
            request.headers.get(TRACE_HEADER) or request.headers.get(TRACE_HEADER_ALT) or new_id("trace")
        )
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_id("req")
        request.state.trace_id = trace_id
        request.state.request_id = request_id
        started = time.perf_counter()
        response: Response
        fault = None
        if not request.url.path.startswith(("/_sim", "/health", "/docs", "/openapi")):
            fault = faults.match(request.url.path)
            if fault is None and settings.fault_rate > 0 and rng.random() < settings.fault_rate:
                fault = {"mode": "error", "status_code": 503, "delay_ms": 0}
        if settings.latency_ms:
            await asyncio.sleep(settings.latency_ms / 1000)
        if fault and fault["mode"] in {"timeout", "latency"}:
            await asyncio.sleep((fault.get("delay_ms") or 30000) / 1000)
        if fault and fault["mode"] == "error":
            response = _error_response(
                fault["status_code"],
                "INJECTED_FAULT" if fault["status_code"] != 429 else "RATE_LIMITED",
                "Simulated upstream failure",
                trace_id,
            )
            if fault["status_code"] == 429:
                response.headers["Retry-After"] = "1"
        else:
            response = await call_next(request)
        duration_ms = round((time.perf_counter() - started) * 1000, 1)
        response.headers[TRACE_HEADER] = trace_id
        response.headers[REQUEST_ID_HEADER] = request_id
        response.headers["x-response-time-ms"] = str(duration_ms)
        if not request.url.path.startswith("/_sim"):
            entry = {
                "ts": datetime.now().astimezone().isoformat(),
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "trace_id": trace_id,
                "request_id": request_id,
                "client_id": request.headers.get(CLIENT_ID_HEADER),
                "metadata_tag": request.headers.get(METADATA_TAG_HEADER),
                "duration_ms": duration_ms,
                "fault": fault["mode"] if fault else None,
            }
            request_log.append(entry)
            log.info("request", extra={k: v for k, v in entry.items() if k != "ts"})
        return response

    # ------------------------------------------------------------------ error handling
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return _error_response(exc.status, exc.code, exc.message, request.state.trace_id, exc.details)

    @app.exception_handler(NotFound)
    async def _not_found(request: Request, exc: NotFound) -> JSONResponse:
        return _error_response(404, "NOT_FOUND", str(exc), request.state.trace_id)

    @app.exception_handler(InvalidRequest)
    async def _invalid(request: Request, exc: InvalidRequest) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", str(exc), request.state.trace_id)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return _error_response(
            422, "VALIDATION_ERROR", "Request validation failed", request.state.trace_id, details
        )

    # ------------------------------------------------------------------ auth
    def require_auth(request: Request) -> None:
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            raise ApiError(401, "UNAUTHORIZED", "Missing bearer token")
        if not any(secrets.compare_digest(token, t) for t in tokens):
            raise ApiError(401, "UNAUTHORIZED", "Invalid bearer token")

    auth = [Depends(require_auth)]

    # ------------------------------------------------------------------ endpoints
    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "service": "alarm-management-api-simulator",
            "version": __version__,
            "dataset": {
                "assets": len(ASSETS),
                "alarms": len(store.alarms),
                "data_start": store.start.isoformat(),
                "data_end": store.end.isoformat(),
            },
        }

    @app.get("/assets/search", dependencies=auth)
    async def search_assets(
        query: str = Query(..., min_length=1, max_length=120),
        site: str | None = Query(None, max_length=64),
        unit: str | None = Query(None, max_length=64),
        asset_type: str | None = Query(None, max_length=64),
        limit: int = Query(10, ge=1, le=100),
    ) -> dict[str, Any]:
        results = store.search_assets(query, site, unit, asset_type, limit)
        return {"query": query, "total": len(results), "results": results}

    @app.get("/assets/{asset_id}/metadata", dependencies=auth)
    async def asset_metadata(asset_id: AssetId) -> dict[str, Any]:
        return store.asset_metadata(asset_id)

    @app.get("/alarms", dependencies=auth)
    async def list_alarms(
        asset_id: AssetId | None = None,
        site: str | None = Query(None, max_length=64),
        unit: str | None = Query(None, max_length=64),
        status: Literal["active", "cleared"] | None = None,
        severity: list[Literal["low", "medium", "high", "critical"]] | None = Query(None),
        alarm_type: str | None = Query(None, max_length=32),
        alarm_name: str | None = Query(None, max_length=120),
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        page: int = Query(1, ge=1, le=100000),
        page_size: int = Query(50, ge=1, le=500),
        sort_by: str = Query("start_time"),
        sort_order: Literal["asc", "desc"] = "desc",
    ) -> dict[str, Any]:
        if start_time and end_time and end_time <= start_time:
            raise ApiError(400, "INVALID_REQUEST", "end_time must be after start_time")
        scope = Scope(
            asset_ids=[asset_id] if asset_id else None,
            site=site,
            unit=unit,
            status=status,
            severities=list(severity) if severity else None,
            alarm_types=[alarm_type] if alarm_type else None,
            alarm_names=[alarm_name] if alarm_name else None,
            start=start_time,
            end=end_time,
        )
        return store.list_alarms(scope, page, page_size, sort_by, sort_order)

    @app.get("/alarms/{alarm_id}", dependencies=auth)
    async def get_alarm(alarm_id: str) -> dict[str, Any]:
        return store.alarm(alarm_id).to_dict(store.end)

    @app.post("/alarms/summary", dependencies=auth)
    async def summary(body: SummaryRequest) -> dict[str, Any]:
        scope = _scope(body, severities=body.severity, alarm_types=body.alarm_types)
        result = store.summary(scope, list(body.group_by), list(body.kpis))
        return {"time_range": body.time_range.model_dump(mode="json"), "filters": _filters(body), **result}

    @app.post("/alarms/trends", dependencies=auth)
    async def trends(body: TrendsRequest) -> dict[str, Any]:
        scope = _scope(body, severities=body.severity)
        result = store.trends(scope, body.bucket, list(body.metrics))
        return {"time_range": body.time_range.model_dump(mode="json"), "filters": _filters(body), **result}

    @app.post("/alarms/correlation", dependencies=auth)
    async def correlation(body: CorrelationRequest) -> dict[str, Any]:
        result = store.correlation(
            body.asset_ids,
            body.time_range.start_time,
            body.time_range.end_time,
            body.lag_window_minutes,
            body.severity_threshold,
            body.min_support,
            body.include_related,
            body.correlation_method,
        )
        return {
            "time_range": body.time_range.model_dump(mode="json"),
            "lag_window_minutes": body.lag_window_minutes,
            **result,
        }

    @app.post("/alarms/flood-analysis", dependencies=auth)
    async def flood(body: FloodRequest) -> dict[str, Any]:
        scope = Scope(
            asset_ids=body.asset_ids,
            site=body.site,
            unit=body.unit,
            start=body.time_range.start_time,
            end=body.time_range.end_time,
        )
        return {
            "time_range": body.time_range.model_dump(mode="json"),
            "unit": body.unit,
            "site": body.site,
            **store.flood_analysis(scope, body.threshold_count, body.rolling_window_minutes),
        }

    @app.post("/alarms/rationalization-candidates", dependencies=auth)
    async def rationalization(body: RationalizationRequest) -> dict[str, Any]:
        return {
            "time_range": body.time_range.model_dump(mode="json"),
            "filters": _filters(body),
            **store.rationalization_candidates(
                _scope(body), body.recurrence_threshold, body.stale_minutes_threshold
            ),
        }

    @app.post("/alarms/priority-score", dependencies=auth)
    async def priority(body: PriorityRequest) -> dict[str, Any]:
        return store.priority_score(body.alarm_id)

    @app.post("/recommendations/operator-actions", dependencies=auth)
    async def recommendations(body: RecommendationRequest) -> dict[str, Any]:
        return operator_actions(
            store,
            body.alarm_id,
            body.include_related,
            body.include_asset_context,
            body.include_historical_pattern,
        )

    @app.post("/calculation-code/generate", dependencies=auth)
    async def calc_generate(body: CalcGenerateRequest) -> dict[str, Any]:
        return store.generate_calculation(
            body.calculation_type, body.filters.model_dump(mode="json", exclude_none=True)
        )

    @app.post("/calculation-code/execute", dependencies=auth)
    async def calc_execute(body: CalcExecuteRequest) -> dict[str, Any]:
        f = body.filters.model_dump(mode="json", exclude_none=True) if body.filters else None
        return store.execute_calculation(body.calculation_id, f)

    @app.get("/analytics/kpi-definitions", dependencies=auth)
    async def kpi_definitions() -> dict[str, Any]:
        return {"kpis": [{"calculation_type": k, **v} for k, v in KPI_DEFINITIONS.items()]}

    # ------------------------------------------------------------------ simulator admin (test harness only)
    @app.get("/_sim/requests", dependencies=auth)
    async def sim_requests(
        limit: int = Query(50, ge=1, le=500), trace_id: str | None = None
    ) -> dict[str, Any]:
        rows = [r for r in request_log if trace_id is None or r["trace_id"] == trace_id]
        return {"requests": rows[-limit:]}

    @app.get("/_sim/faults", dependencies=auth)
    async def list_faults() -> dict[str, Any]:
        return {"rules": faults.rules}

    @app.post("/_sim/faults", dependencies=auth)
    async def add_fault(rule: FaultRule) -> dict[str, Any]:
        faults.rules.append(rule.model_dump())
        return {"rules": faults.rules}

    @app.delete("/_sim/faults", dependencies=auth)
    async def clear_faults() -> dict[str, Any]:
        faults.rules.clear()
        return {"rules": []}

    return app


def _scope(body: Any, **extra: Any) -> Scope:
    return Scope(
        asset_ids=body.asset_ids,
        site=body.site,
        unit=body.unit,
        start=body.time_range.start_time,
        end=body.time_range.end_time,
        severities=list(extra["severities"]) if extra.get("severities") else None,
        alarm_types=list(extra["alarm_types"]) if extra.get("alarm_types") else None,
    )


def _filters(body: Any) -> dict[str, Any]:
    return {k: v for k, v in {"asset_ids": body.asset_ids, "site": body.site, "unit": body.unit}.items() if v}


def _error_response(status: int, code: str, message: str, trace_id: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message, "trace_id": trace_id}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=body, headers={TRACE_HEADER: trace_id})
