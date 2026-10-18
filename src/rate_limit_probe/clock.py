"""Time, injected everywhere so tests and the server can share a fake clock.

now() is monotonic seconds for measuring intervals; wall() is Unix time for
HTTP dates and epoch-style reset headers.
"""

import threading
import time


class RealClock:
    def now(self):
        return time.monotonic()

    def wall(self):
        return time.time()

    def sleep(self, seconds):
        if seconds > 0:
            time.sleep(seconds)


class FakeClock:
    """A clock that only moves when something sleeps. Safe to share between
    the probe and the server's request thread."""

    def __init__(self, wall_start=1_790_000_000.0):
        self._t = 0.0
        self._wall_start = wall_start
        self._lock = threading.Lock()

    def now(self):
        with self._lock:
            return self._t

    def wall(self):
        with self._lock:
            return self._wall_start + self._t

    def sleep(self, seconds):
        if seconds > 0:
            with self._lock:
                self._t += seconds
