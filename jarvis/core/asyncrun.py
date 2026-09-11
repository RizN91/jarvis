"""Run coroutines from synchronous code on one shared background loop.

The Windows integration (hooks, overlay, tray, audio callbacks) is synchronous
and thread-based; the cloud engines are asyncio. Rather than nesting
asyncio.run() per call (which creates and tears down a loop and an event loop
policy on every utterance), the app owns ONE loop on a dedicated daemon thread.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future
from typing import Any, Coroutine, Optional


class AsyncRunner:
    def __init__(self, name: str = "asyncio"):
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._name = name

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(target=self._run, name=self._name,
                                                daemon=True)
                self._thread.start()
            return self._loop

    def _run(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def submit(self, coro: Coroutine) -> Future:
        """Schedule a coroutine; returns a concurrent Future."""
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def run(self, coro: Coroutine, timeout: Optional[float] = None) -> Any:
        future = self.submit(coro)
        try:
            return future.result(timeout)
        except TimeoutError:
            future.cancel()
            raise

    def stop(self) -> None:
        with self._lock:
            loop = self._loop
        if loop and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(timeout=2.0)


RUNNER = AsyncRunner()
