"""Streaming summary analysis with progressive chunk events."""

import json
import logging
import re
import threading
from dataclasses import dataclass

from src.ai.event_bus import MatchEvent
from src.ai.prompts import SUMMARY_SYSTEM_PROMPT

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Custom exceptions (shared with B5 Matcher)
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    """Raised when user cancels the match operation."""
    pass


class AuthFailedError(Exception):
    """Raised when API authentication fails."""
    pass


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class SummaryResult:
    """Result of a streaming summary analysis."""

    structured: dict | None   # Parsed JSON from LLM output
    raw: str                  # Full raw text


# ---------------------------------------------------------------------------
# SummaryStreamer
# ---------------------------------------------------------------------------

class SummaryStreamer:
    """Stream summary analysis with progressive chunk events."""

    def __init__(self, ai_client, bus, cancel_event):
        self.ai_client = ai_client    # AIClient instance
        self.bus = bus                # EventBus instance
        self.cancel = cancel_event    # threading.Event for cancellation

    def stream(self, user_prompt: str) -> SummaryResult:
        """Stream LLM summary, emit chunk events, return parsed result."""
        buffer = ""

        messages = [
            {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        stream_iter = self.ai_client.stream_chat(messages, temperature=0.4)

        for chunk in stream_iter:
            if self.cancel.is_set():
                stream_iter.close()
                raise CancelledError("用户取消综合分析")

            delta = ""
            if chunk.choices and chunk.choices[0].delta:
                delta = chunk.choices[0].delta.content or ""

            if delta:
                buffer += delta
                self.bus.emit(MatchEvent("summary_chunk", text=delta))

        structured = self._try_parse_json(buffer)

        self.bus.emit(MatchEvent("summary_done",
                                 structured=structured, raw=buffer))

        return SummaryResult(structured=structured, raw=buffer)

    def _try_parse_json(self, text: str) -> dict | None:
        """Two-stage JSON parse: direct -> regex from code block."""
        # Stage 1: direct parse
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass
        # Stage 2: extract from markdown code block
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except (json.JSONDecodeError, ValueError):
                pass
        return None
