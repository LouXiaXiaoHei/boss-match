"""Tests for EventBus and MatchEvent."""

import json
import threading
import time

from src.ai.event_bus import EventBus, MatchEvent


class TestMatchEvent:
    """MatchEvent serialization tests."""

    def test_to_json_basic(self):
        event = MatchEvent("match_start", phase="init")
        payload = json.loads(event.to_json())
        assert payload["type"] == "match_start"
        assert payload["phase"] == "init"

    def test_to_json_with_data(self):
        event = MatchEvent("score", phase="rank", job_id="abc", score=0.9)
        payload = json.loads(event.to_json())
        assert payload["type"] == "score"
        assert payload["phase"] == "rank"
        assert payload["job_id"] == "abc"
        assert payload["score"] == 0.9

    def test_to_json_empty_phase(self):
        event = MatchEvent("done")
        payload = json.loads(event.to_json())
        assert payload["type"] == "done"
        assert payload["phase"] == ""

    def test_to_json_chinese_content(self):
        event = MatchEvent("match", phase="匹配", result="通过")
        text = event.to_json()
        payload = json.loads(text)
        assert payload["result"] == "通过"
        assert "通过" in text

    def test_slots(self):
        event = MatchEvent("test")
        assert not hasattr(event, "__dict__")
        assert hasattr(event, "__slots__")


class TestEventBus:
    """EventBus dispatch and lifecycle tests."""

    def test_concurrent_emit_serial_dispatch_order(self):
        """100 events emitted from multiple threads must be dispatched
        in FIFO order by the single consumer thread."""
        dispatched = []
        lock = threading.Lock()

        def notify(payload_json: str):
            payload = json.loads(payload_json)
            with lock:
                dispatched.append(payload["seq"])

        bus = EventBus(notify_callback=notify)
        bus.start()

        n_events = 100
        n_threads = 10

        def emit_range(start, count):
            for i in range(start, start + count):
                bus.emit(MatchEvent("tick", phase="test", seq=i))

        threads = []
        per_thread = n_events // n_threads
        for t in range(n_threads):
            th = threading.Thread(target=emit_range, args=(t * per_thread, per_thread))
            threads.append(th)

        for th in threads:
            th.start()
        for th in threads:
            th.join()

        # Wait for consumer to process all events
        for _ in range(50):
            with lock:
                if len(dispatched) == n_events:
                    break
            time.sleep(0.05)

        bus.stop()

        assert len(dispatched) == n_events, f"Expected {n_events}, got {len(dispatched)}"
        # FIFO: each emit is a queue.put, consumer does queue.get
        assert dispatched == sorted(dispatched), "Dispatch order must be FIFO"

    def test_stop_exits_consumer_thread(self):
        """stop() must cause the consumer thread to exit cleanly."""
        bus = EventBus(notify_callback=lambda _: None)
        bus.start()
        thread = bus._thread
        assert thread.is_alive()
        bus.stop()
        assert not thread.is_alive()

    def test_empty_queue_stop(self):
        """stop() on a bus with no events should exit cleanly."""
        bus = EventBus(notify_callback=lambda _: None)
        bus.start()
        time.sleep(0.05)
        bus.stop()
        assert not bus._thread.is_alive()

    def test_emit_after_stop_does_not_crash(self):
        """Emitting after stop should not raise — events just queue up."""
        bus = EventBus(notify_callback=lambda _: None)
        bus.start()
        bus.stop()
        bus.emit(MatchEvent("late", phase="after_stop"))

    def test_notify_receives_correct_json(self):
        """The notify callback must receive the exact JSON from MatchEvent.to_json()."""
        received = []
        ready = threading.Event()

        def notify(payload_json: str):
            received.append(payload_json)
            if len(received) == 2:
                ready.set()

        bus = EventBus(notify_callback=notify)
        bus.start()
        bus.emit(MatchEvent("match_start", phase="init", job_id="j1"))
        bus.emit(MatchEvent("match_done", phase="end", count=5))
        ready.wait(timeout=2.0)
        bus.stop()

        assert len(received) == 2
        p0 = json.loads(received[0])
        assert p0["type"] == "match_start"
        assert p0["phase"] == "init"
        assert p0["job_id"] == "j1"
        p1 = json.loads(received[1])
        assert p1["type"] == "match_done"
        assert p1["phase"] == "end"
        assert p1["count"] == 5

    def test_notify_exception_does_not_kill_consumer(self):
        """A failing notify callback must not crash the consumer loop."""
        call_count = 0
        lock = threading.Lock()
        all_done = threading.Event()

        def flaky_notify(payload_json: str):
            nonlocal call_count
            with lock:
                call_count += 1
                if call_count == 1:
                    raise RuntimeError("simulated failure")
                if call_count == 3:
                    all_done.set()

        bus = EventBus(notify_callback=flaky_notify)
        bus.start()
        bus.emit(MatchEvent("first"))
        bus.emit(MatchEvent("second"))
        bus.emit(MatchEvent("third"))
        all_done.wait(timeout=2.0)
        bus.stop()

        with lock:
            assert call_count == 3, f"Expected 3 calls, got {call_count}"

    def test_stop_drains_remaining_events(self):
        """Events queued before stop() must all be dispatched (no event loss)."""
        dispatched = []
        lock = threading.Lock()

        def notify(payload_json: str):
            payload = json.loads(payload_json)
            with lock:
                dispatched.append(payload["seq"])

        bus = EventBus(notify_callback=notify)
        bus.start()

        # Emit 10 events then immediately stop
        for i in range(10):
            bus.emit(MatchEvent("tick", phase="drain_test", seq=i))
        bus.stop()

        with lock:
            assert len(dispatched) == 10, f"Expected 10, got {len(dispatched)} — events lost on stop"

    def test_double_start_raises(self):
        """Calling start() while already running must raise RuntimeError."""
        bus = EventBus(notify_callback=lambda _: None)
        bus.start()
        try:
            with pytest.raises(RuntimeError):
                bus.start()
        finally:
            bus.stop()

    def test_stop_before_start_is_noop(self):
        """Calling stop() before start() must not raise or leave stale sentinel."""
        bus = EventBus(notify_callback=lambda _: None)
        bus.stop()  # should be a no-op

        # Now start should work correctly
        dispatched = []
        ready = threading.Event()

        def notify(payload_json: str):
            dispatched.append(payload_json)
            ready.set()

        bus2 = EventBus(notify_callback=notify)
        bus2.start()
        bus2.emit(MatchEvent("test"))
        ready.wait(timeout=2.0)
        bus2.stop()
        assert len(dispatched) == 1


import pytest  # noqa: E402 — needed for pytest.raises in test_double_start_raises
