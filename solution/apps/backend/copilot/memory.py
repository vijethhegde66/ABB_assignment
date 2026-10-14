"""Conversation memory: lets follow-up questions refer to "this alarm" / "that pump"."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationState:
    conversation_id: str
    assets: list[dict[str, Any]] = field(default_factory=list)
    focal_alarm: dict[str, Any] | None = None
    site: str | None = None
    unit: str | None = None
    lookback_days: int | None = None
    last_intent: str | None = None
    turns: list[dict[str, str]] = field(default_factory=list)
    updated: float = field(default_factory=time.time)


class ConversationStore:
    """Bounded in-memory LRU with TTL. Swap for Redis in a multi-replica deployment."""

    def __init__(self, max_conversations: int = 500, ttl_seconds: int = 4 * 3600) -> None:
        self._data: OrderedDict[str, ConversationState] = OrderedDict()
        self._lock = threading.Lock()
        self.max = max_conversations
        self.ttl = ttl_seconds

    def get(self, conversation_id: str) -> ConversationState:
        with self._lock:
            now = time.time()
            state = self._data.get(conversation_id)
            if state is None or now - state.updated > self.ttl:
                state = ConversationState(conversation_id)
            self._data[conversation_id] = state
            self._data.move_to_end(conversation_id)
            while len(self._data) > self.max:
                self._data.popitem(last=False)
            return state

    def peek(self, conversation_id: str) -> ConversationState | None:
        with self._lock:
            return self._data.get(conversation_id)

    def save(self, state: ConversationState) -> None:
        state.updated = time.time()
        state.turns = state.turns[-20:]
        with self._lock:
            self._data[state.conversation_id] = state
