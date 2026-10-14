"""Trace context propagated across GUI -> copilot -> MCP client -> MCP server -> Alarm API.

The context lives in a ``contextvars.ContextVar`` so that asyncio tasks spawned
while handling a request inherit it automatically and log lines can be
correlated without passing ids through every function signature.
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field, replace

# Header names used on the HTTP hops. ``trace_id`` / ``x-client-id`` /
# ``x-metadata-tag`` are the names defined by the Alarm API Postman contract.
TRACE_HEADER = "trace_id"
TRACE_HEADER_ALT = "x-trace-id"
REQUEST_ID_HEADER = "x-request-id"
CLIENT_ID_HEADER = "x-client-id"
METADATA_TAG_HEADER = "x-metadata-tag"
CONVERSATION_HEADER = "x-conversation-id"


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:16]}"


@dataclass(frozen=True)
class TraceContext:
    trace_id: str
    request_id: str = field(default_factory=lambda: new_id("req"))
    conversation_id: str | None = None
    client_id: str | None = None
    metadata_tag: str | None = None

    def child(self, **changes: str | None) -> TraceContext:
        """Same trace, new request id (one per hop / tool call)."""
        return replace(self, request_id=new_id("req"), **changes)  # type: ignore[arg-type]

    def as_mcp_meta(self) -> dict[str, str]:
        meta = {"trace_id": self.trace_id, "request_id": self.request_id}
        if self.conversation_id:
            meta["conversation_id"] = self.conversation_id
        return meta

    def as_log_fields(self) -> dict[str, str]:
        fields = {"trace_id": self.trace_id, "request_id": self.request_id}
        if self.conversation_id:
            fields["conversation_id"] = self.conversation_id
        return fields


_current: ContextVar[TraceContext | None] = ContextVar("trace_context", default=None)


def current_trace() -> TraceContext | None:
    return _current.get()


@contextlib.contextmanager
def trace_scope(ctx: TraceContext) -> Iterator[TraceContext]:
    token = _current.set(ctx)
    try:
        yield ctx
    finally:
        _current.reset(token)
