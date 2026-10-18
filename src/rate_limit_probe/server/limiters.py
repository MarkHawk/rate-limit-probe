"""Rate-limit algorithms, and wrappers that misbehave on purpose.

Every limiter answers check(now) with a Decision. now is the shared clock's
monotonic time. Rejected requests never consume quota, except where a wrapper
says otherwise.
"""

import collections
import dataclasses
import math


@dataclasses.dataclass(frozen=True)
class Decision:
    allowed: bool
    limit: int
    remaining: int
    # Seconds until the quota is fully restored.
    reset_after: float
    # Seconds until a request would be allowed; 0 when allowed.
    retry_after: float = 0.0
    # An HTTP status that replaces the normal 200 / 429, such as 403 for a block.
    status: int | None = None


class FixedWindow:
    """limit requests per window, counted in windows aligned to the first check."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.origin = None
        self.index = None
        self.count = 0

    def check(self, now):
        if self.origin is None:
            self.origin = now
        index = math.floor((now - self.origin) / self.window)
        if index != self.index:
            self.index, self.count = index, 0
        window_end = self.origin + (index + 1) * self.window
        left = window_end - now
        if self.count < self.limit:
            self.count += 1
            return Decision(True, self.limit, self.limit - self.count, left)
        return Decision(False, self.limit, 0, left, retry_after=left)


class SlidingWindow:
    """At most limit requests in any window seconds (a sliding log)."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.log = collections.deque()

    def check(self, now):
        while self.log and self.log[0] <= now - self.window:
            self.log.popleft()
        if len(self.log) < self.limit:
            self.log.append(now)
            return Decision(True, self.limit, self.limit - len(self.log), self.log[-1] + self.window - now)
        return Decision(
            False, self.limit, 0, self.log[-1] + self.window - now,
            retry_after=self.log[0] + self.window - now,
        )


class TokenBucket:
    """A bucket of limit tokens that refills completely over window seconds."""

    def __init__(self, limit, window):
        self.limit, self.window = limit, window
        self.rate = limit / window
        self.tokens = float(limit)
        self.last = None

    def check(self, now):
        if self.last is not None:
            self.tokens = min(self.limit, self.tokens + (now - self.last) * self.rate)
        self.last = now
        if self.tokens >= 1:
            self.tokens -= 1
            return Decision(True, self.limit, math.floor(self.tokens), (self.limit - self.tokens) / self.rate)
        return Decision(
            False, self.limit, 0, (self.limit - self.tokens) / self.rate,
            retry_after=(1 - self.tokens) / self.rate,
        )


class Penalty:
    """Any request sent while limited restarts the lockout. A client that
    retries without waiting therefore never gets out."""

    def __init__(self, inner, lockout):
        self.inner, self.lockout = inner, lockout
        self.locked_until = None

    def check(self, now):
        if self.locked_until is not None and now < self.locked_until:
            self.locked_until = now + self.lockout
            return Decision(False, self.inner.limit, 0, self.lockout, retry_after=self.lockout)
        self.locked_until = None
        decision = self.inner.check(now)
        if not decision.allowed:
            self.locked_until = now + self.lockout
            return dataclasses.replace(decision, retry_after=self.lockout, reset_after=self.lockout)
        return decision


class Block:
    """After block_after refused requests, answer 403 to everything."""

    def __init__(self, inner, block_after):
        self.inner, self.block_after = inner, block_after
        self.limit = inner.limit
        self.refused = 0

    def check(self, now):
        if self.refused >= self.block_after:
            return Decision(False, self.limit, 0, 0.0, status=403)
        decision = self.inner.check(now)
        if not decision.allowed:
            self.refused += 1
        return decision


ALGORITHMS = {"fixed-window": FixedWindow, "sliding-window": SlidingWindow, "token-bucket": TokenBucket}
