"""Unit coverage for LiveTradingRunner reconnect backoff (no Gateway needed)."""

from __future__ import annotations

import threading
from types import SimpleNamespace

from marketmind.live_trading import LiveTradingRunner


class _FlakyAdapter:
    def __init__(self, fail_times: int = 2) -> None:
        self.fail_times = fail_times
        self.calls = 0
        self._connected = False

    def health_check(self) -> bool:
        return self._connected

    def reconnect(self):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError(f"boom-{self.calls}")
        self._connected = True
        return SimpleNamespace(account="DU123")


def test_ensure_connection_retries_then_succeeds(monkeypatch) -> None:
    runner = LiveTradingRunner.__new__(LiveTradingRunner)
    runner._adapter = _FlakyAdapter(fail_times=2)
    runner._running = True
    runner._stop_event = threading.Event()
    runner._lock = threading.Lock()
    runner._last_error = None
    # Speed up backoff waits in the test.
    waits: list[float] = []

    def _wait(seconds: float) -> bool:
        waits.append(seconds)
        return False

    runner._stop_event.wait = _wait  # type: ignore[method-assign]
    assert runner._ensure_connection() is True
    assert runner._adapter.calls == 3
    assert waits == [1.0, 2.0]
    assert runner._last_error is None


def test_ensure_connection_gives_up_after_backoff(monkeypatch) -> None:
    runner = LiveTradingRunner.__new__(LiveTradingRunner)
    runner._adapter = _FlakyAdapter(fail_times=99)
    runner._running = True
    runner._stop_event = threading.Event()
    runner._lock = threading.Lock()
    runner._last_error = None
    runner._stop_event.wait = lambda _seconds: False  # type: ignore[method-assign]
    assert runner._ensure_connection() is False
    assert runner._adapter.calls == 4
    assert runner._last_error and "boom-4" in runner._last_error
