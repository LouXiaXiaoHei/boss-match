"""Event bus for match event dispatching — single-thread serial consumer over queue."""

import json
import logging
import queue
import threading

log = logging.getLogger(__name__)


class MatchEvent:
    """Lightweight event object carried through the bus."""

    __slots__ = ("type", "phase", "data")

    def __init__(self, type: str, phase: str = "", **data):
        self.type = type
        self.phase = phase
        self.data = data

    def to_json(self) -> str:
        """Serialize to JSON string with type, phase, and all data fields."""
        payload = {"type": self.type, "phase": self.phase}
        payload.update(self.data)
        return json.dumps(payload, ensure_ascii=False)


class EventBus:
    """Single-producer / multi-producer, single-consumer event bus.

    Producers call ``emit(event)`` which enqueues and returns immediately.
    A dedicated consumer thread dequeues events serially and pushes each
    via the ``notify_callback`` supplied at construction time.
    """

    def __init__(self, notify_callback):
        self._notify = notify_callback
        self._queue: queue.Queue[MatchEvent | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self):
        """Clear stop flag and launch the consumer daemon thread."""
        self._stop.clear()
        self._thread = threading.Thread(target=self._consume, name="event-bus", daemon=True)
        self._thread.start()
        log.info("EventBus consumer started")

    def stop(self):
        """Signal the consumer to stop and enqueue the sentinel."""
        self._stop.set()
        self._queue.put(None)

    def emit(self, event: MatchEvent):
        """Enqueue an event — returns immediately (non-blocking)."""
        self._queue.put(event)

    def _consume(self):
        """Consumer loop: serially dequeue events and notify frontend."""
        while not self._stop.is_set():
            event = self._queue.get()
            if event is None:
                break
            try:
                self._notify(event.to_json())
            except Exception:
                log.exception("EventBus notify error for event type=%s", event.type)
        log.info("EventBus consumer stopped")
