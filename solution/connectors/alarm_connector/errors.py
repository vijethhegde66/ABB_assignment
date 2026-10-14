"""Typed error hierarchy: every upstream failure maps to exactly one class with a stable code."""

from __future__ import annotations

from typing import Any


class AlarmApiError(Exception):
    code = "UPSTREAM_ERROR"
    retryable = False

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        attempts: int = 1,
        trace_id: str | None = None,
        details: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.attempts = attempts
        self.trace_id = trace_id
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.code,
            "message": self.message,
            "retryable": self.retryable,
            "status_code": self.status_code,
            "attempts": self.attempts,
            "trace_id": self.trace_id,
            "details": self.details,
        }


class AuthenticationError(AlarmApiError):
    code = "AUTHENTICATION_FAILED"


class NotFoundError(AlarmApiError):
    code = "NOT_FOUND"


class InvalidArgumentError(AlarmApiError):
    code = "INVALID_ARGUMENT"


class RateLimitedError(AlarmApiError):
    code = "RATE_LIMITED"
    retryable = True


class UpstreamUnavailableError(AlarmApiError):
    code = "UPSTREAM_UNAVAILABLE"
    retryable = True


class UpstreamTimeoutError(AlarmApiError):
    code = "UPSTREAM_TIMEOUT"
    retryable = True


class BadUpstreamResponseError(AlarmApiError):
    code = "BAD_UPSTREAM_RESPONSE"


def error_for_status(status: int) -> type[AlarmApiError]:
    if status in (401, 403):
        return AuthenticationError
    if status == 404:
        return NotFoundError
    if status in (400, 409, 422):
        return InvalidArgumentError
    if status == 429:
        return RateLimitedError
    if status in (502, 503, 504):
        return UpstreamUnavailableError
    return AlarmApiError
