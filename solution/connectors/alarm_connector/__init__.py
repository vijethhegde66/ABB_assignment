"""Reusable, typed async connector for the Alarm Management API.

Responsibilities: authentication, timeouts, retry with exponential backoff,
trace-header propagation, error mapping and pagination. It is used by the
MCP server; the copilot never talks to the Alarm API directly.
"""

from alarm_connector.client import AlarmApiClient, ApiResponse, CallRecord
from alarm_connector.config import AlarmApiSettings
from alarm_connector.errors import (
    AlarmApiError,
    AuthenticationError,
    BadUpstreamResponseError,
    InvalidArgumentError,
    NotFoundError,
    RateLimitedError,
    UpstreamTimeoutError,
    UpstreamUnavailableError,
)

__all__ = [
    "AlarmApiClient",
    "AlarmApiError",
    "AlarmApiSettings",
    "ApiResponse",
    "AuthenticationError",
    "BadUpstreamResponseError",
    "CallRecord",
    "InvalidArgumentError",
    "NotFoundError",
    "RateLimitedError",
    "UpstreamTimeoutError",
    "UpstreamUnavailableError",
]
