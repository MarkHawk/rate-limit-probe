"""Named rate-limit behaviours, shared by `serve`, `demo` and the tests."""

import dataclasses
import math

from rate_limit_probe.server import emit
from rate_limit_probe.server.limiters import ALGORITHMS, Block, Penalty


@dataclasses.dataclass(frozen=True)
class Scenario:
    name: str
    summary: str
    algorithm: str = "fixed-window"
    limit: int = 5
    window: float = 10.0
    headers: tuple = ("retry-after-seconds",)
    # Early requests restart the lockout.
    penalty: bool = False
    # Retry-After is this fraction of the real wait (below 1 means it lies).
    retry_after_scale: float = 1.0
    # X-RateLimit-Remaining / RateLimit r never count down.
    stale_remaining: bool = False
    # Answer 403 after this many refusals.
    block_after: int | None = None
    # The status used to refuse: 429 normally, 503 for an overload-style limit.
    refuse_status: int = 429

    def __post_init__(self):
        if self.algorithm not in ALGORITHMS:
            raise ValueError(f"unknown algorithm {self.algorithm!r}")
        for style in self.headers:
            if style not in emit.STYLES:
                raise ValueError(f"unknown header style {style!r}")
        if self.limit < 1 or self.window <= 0:
            raise ValueError("limit must be at least 1 and window above 0")


SCENARIOS = (
    Scenario("fixed-seconds", "fixed window, Retry-After seconds"),
    Scenario("sliding-date", "sliding window, Retry-After date", algorithm="sliding-window",
             headers=("retry-after-date",)),
    Scenario("bucket-ietf", "token bucket, IETF RateLimit", algorithm="token-bucket",
             headers=("ietf", "retry-after-seconds")),
    Scenario("xrl", "fixed window, X-RateLimit-*", headers=("x-ratelimit",)),
    Scenario("none", "fixed window, no headers", headers=("none",)),
    Scenario("lying", "Retry-After shorter than reality", retry_after_scale=0.2),
    Scenario("stale-remaining", "Remaining never counts down", headers=("x-ratelimit", "retry-after-seconds"),
             stale_remaining=True),
    Scenario("penalty", "early retries restart the limit", headers=("retry-after-seconds", "x-ratelimit"),
             penalty=True),
    Scenario("block", "403 after repeated 429s", block_after=2),
    Scenario("overload-503", "503 with Retry-After instead of 429", refuse_status=503),
)

BY_NAME = {s.name: s for s in SCENARIOS}


class Endpoint:
    """One rate-limited resource: a limiter plus how it reports itself."""

    def __init__(self, scenario):
        self.scenario = scenario
        limiter = ALGORITHMS[scenario.algorithm](scenario.limit, scenario.window)
        if scenario.penalty:
            limiter = Penalty(limiter, lockout=scenario.window)
        if scenario.block_after is not None:
            limiter = Block(limiter, scenario.block_after)
        self.limiter = limiter

    def respond(self, now, wall):
        """(status, headers, body) for one request at the given time."""
        s = self.scenario
        decision = self.limiter.check(now)
        headers = emit.rate_headers(
            decision, s.headers, wall, retry_after_scale=s.retry_after_scale, stale_remaining=s.stale_remaining,
        )
        headers = [(k, v.replace("{window}", str(math.ceil(s.window)))) for k, v in headers]
        if decision.status is not None:
            return decision.status, headers, b"blocked\n"
        if decision.allowed:
            return 200, headers, b"ok\n"
        if s.refuse_status == 503:
            return 503, headers, b"overloaded, try later\n"
        return 429, headers, b"too many requests\n"


def scaled(scenario, window):
    """The same behaviour with a different window, such as the demo's short one."""
    return dataclasses.replace(scenario, window=window)
