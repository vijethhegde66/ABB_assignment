import json
import logging
import time

from copilot.mcp_client import parse_tool_error
from copilot.memory import ConversationStore
from platform_common import TraceContext, redact, trace_scope
from platform_common.logging import JsonFormatter


def test_redact_masks_sensitive_keys_and_values() -> None:
    data = {
        "Authorization": "Bearer abc.def",
        "nested": {"api_key": "x", "note": "token=supersecret&x=1"},
        "list": ["Bearer zzz", "sk-ant-1234567890abcdef"],
        "ok": "plain",
    }
    out = redact(data)
    assert out["Authorization"] == "***REDACTED***"
    assert out["nested"]["api_key"] == "***REDACTED***"
    assert "supersecret" not in out["nested"]["note"]
    assert all("REDACTED" in v for v in out["list"])
    assert out["ok"] == "plain"


def test_json_formatter_includes_trace_and_redacts() -> None:
    fmt = JsonFormatter("svc")
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "calling with Bearer secret-token", None, None)
    record.token = "abc"
    with trace_scope(TraceContext(trace_id="trace-1", conversation_id="conv-1")):
        line = json.loads(fmt.format(record))
    assert line["trace_id"] == "trace-1" and line["conversation_id"] == "conv-1"
    assert "secret-token" not in line["msg"] and line["token"] == "***REDACTED***"


def test_trace_child_keeps_trace_id() -> None:
    t = TraceContext(trace_id="trace-a", conversation_id="c")
    child = t.child()
    assert child.trace_id == "trace-a" and child.request_id != t.request_id
    assert child.as_mcp_meta()["conversation_id"] == "c"


def test_parse_tool_error_variants() -> None:
    assert (
        parse_tool_error('Error executing tool x: {"error_code": "NOT_FOUND", "message": "m"}')["error_code"]
        == "NOT_FOUND"
    )
    assert (
        parse_tool_error("Error executing tool x: 1 validation error for xArguments\nfoo")["error_code"]
        == "INVALID_ARGUMENT"
    )
    assert parse_tool_error("Unknown tool: nope")["error_code"] == "TOOL_UNAVAILABLE"
    assert parse_tool_error("something odd")["error_code"] == "TOOL_ERROR"


def test_conversation_store_lru_and_ttl() -> None:
    store = ConversationStore(max_conversations=2, ttl_seconds=60)
    a = store.get("a")
    a.site = "X"
    store.save(a)
    store.get("b")
    store.get("c")
    assert store.peek("a") is None  # evicted (LRU)
    c = store.get("c")
    c.site = "Y"
    c.updated = time.time() - 120
    store._data["c"] = c
    assert store.get("c").site is None  # expired
