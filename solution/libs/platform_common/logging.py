"""JSON structured logging with secret redaction.

Every log record is emitted as one JSON object carrying the active trace
context. Any field whose *key* looks sensitive is masked, and any *value*
that looks like a bearer token / API key is masked as well, so secrets can
never leak through ``extra=`` fields or exception messages.
"""

from __future__ import annotations

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

from platform_common.tracing import current_trace

_SENSITIVE_KEY = re.compile(r"(authorization|token|secret|password|api[_-]?key|cookie|credential)", re.I)
_SECRET_VALUE_PATTERNS = [
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"sk-[A-Za-z0-9_-]{8,}"),
    re.compile(r"(?i)(api[_-]?key|token|password)=([^&\s]+)"),
]
MASK = "***REDACTED***"


def _mask_string(value: str) -> str:
    out = value
    for pattern in _SECRET_VALUE_PATTERNS:
        if pattern.groups == 2:
            out = pattern.sub(lambda m: f"{m.group(1)}={MASK}", out)
        else:
            out = pattern.sub(MASK, out)
    return out


def redact(value: Any, _depth: int = 0) -> Any:
    """Return a deep copy of ``value`` with secrets masked."""
    if _depth > 8:
        return "…"
    if isinstance(value, dict):
        return {
            k: (MASK if isinstance(k, str) and _SENSITIVE_KEY.search(k) else redact(v, _depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact(v, _depth + 1) for v in value]
    if isinstance(value, str):
        return _mask_string(value)
    return value


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "service": self.service,
            "logger": record.name,
            "msg": _mask_string(record.getMessage()),
        }
        trace = current_trace()
        if trace is not None:
            payload.update(trace.as_log_fields())
        for key, val in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = val
        if record.exc_info:
            payload["exc"] = _mask_string(self.formatException(record.exc_info))
        return json.dumps(redact(payload), default=str)


def configure_logging(service: str, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)  # stderr: stdout is reserved for MCP stdio transport
    handler.setFormatter(JsonFormatter(service))
    root.addHandler(handler)
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpcore", "httpx2", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
