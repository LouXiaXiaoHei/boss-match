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
    """Multi-producer, single-consumer event bus.

    Producers call ``emit(event)`` which enqueues and returns immediately.
    A dedicated consumer thread dequeues events serially and pushes each
    via the ``notify_callback`` supplied at construction time.

    Lifecycle: ``start()`` → ``emit()`` × N → ``stop()`` (drains remaining events).
    """

    def __init__(self, notify_callback):
        self._notify = notify_callback
        self._queue: queue.Queue[MatchEvent | None] = queue.Queue()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def start(self):
        """Launch the consumer daemon thread. Raises if already running."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("EventBus already running")
            self._stop.clear()
            self._thread = threading.Thread(target=self._consume, name="event-bus", daemon=True)
            self._thread.start()
        log.info("EventBus consumer started")

    def stop(self, timeout: float = 5.0):
        """Signal the consumer to stop, drain remaining events, and wait for exit.

        Blocks until the consumer thread exits or *timeout* seconds elapse.
        Safe to call even if the bus was never started.
        """
        with self._lock:
            thread = self._thread
        if thread is None or not thread.is_alive():
            return
        self._stop.set()
        self._queue.put(None)  # sentinel to unblock queue.get()
        thread.join(timeout=timeout)

    def emit(self, event: MatchEvent):
        """Enqueue an event — returns immediately (non-blocking)."""
        self._queue.put(event)

    def _consume(self):
        """Consumer loop: serially dequeue events and notify frontend.

        Exits on the None sentinel, then drains any remaining queued events
        before stopping so no events are lost on shutdown.
        """
        while True:
            event = self._queue.get()
            if event is None:
                break
            try:
                self._notify(event.to_json())
            except Exception:
                log.exception("EventBus notify error for event type=%s", event.type)
        # Drain remaining events after sentinel
        while not self._queue.empty():
            event = self._queue.get()
            if event is None:
                continue
            try:
                self._notify(event.to_json())
            except Exception:
                log.exception("EventBus notify error for event type=%s", event.type)
        log.info("EventBus consumer stopped")
