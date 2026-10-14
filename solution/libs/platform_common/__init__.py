"""Cross-cutting concerns shared by every service: structured logging, secret
redaction and trace-context propagation."""

from platform_common.logging import configure_logging, get_logger, redact
from platform_common.tracing import (
    TraceContext,
    current_trace,
    new_id,
    trace_scope,
)

__all__ = [
    "TraceContext",
    "configure_logging",
    "current_trace",
    "get_logger",
    "new_id",
    "redact",
    "trace_scope",
]
