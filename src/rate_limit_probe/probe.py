"""The request sequence. Small, slow and in four phases:

  discover  send at the paced rate until the first 429
  early     one request before the server's wait is over, to see whether an
            early retry is refused and whether it extends the limit
  honour    wait as long as the server asks (Retry-After, else a quota reset
            header, else a doubling back-off), then one request; repeat until
            it is accepted or the attempts run out
  refill    send again until the next 429, to see whether the full limit came
            back at once or only part of it

The run stops at the first sign of a block or anything unexpected: any status
other than 2xx or 429, a connection error, a wait longer than allowed, or the
request budget running out.
"""

import dataclasses

from rate_limit_probe import headers as rate_headers
from rate_limit_probe.safety import LimitExceeded

MAX_HONOUR_ATTEMPTS = 8
BACKOFF_FIRST = 1.0
EARLY_WITHOUT_HINT = 1.0
# Requests kept back from discovery for the later phases.
RESERVE = 20


@dataclasses.dataclass(frozen=True)
class Observation:
    n: int  # 1-based request number
    phase: str
    at: float  # seconds since the first request
    status: int | None
    info: rate_headers.RateInfo
    error_kind: str | None = None
    error: str | None = None

    @property
    def ok(self):
        return self.status is not None and 200 <= self.status < 300

    @property
    def limited(self):
        return self.status == 429


@dataclasses.dataclass(frozen=True)
class Wait:
    at: float  # seconds since the first request, when the wait began
    seconds: float
    source: str  # Retry-After, X-RateLimit-Reset, RateLimit, RateLimit-Reset, back-off, early
    purpose: str  # early, honour


@dataclasses.dataclass(frozen=True)
class Stop:
    kind: str  # blocked, overload-503, server-error, redirect, status, connection, wait-too-long, budget
    detail: str
    request: int | None = None
    retry_after: float | None = None


@dataclasses.dataclass
class ProbeResult:
    target: object
    limits: object
    method: str
    events: list  # Observations and Waits, in order
    stop: Stop | None = None
    duration: float = 0.0

    @property
    def observations(self):
        return [e for e in self.events if isinstance(e, Observation)]

    @property
    def waits(self):
        return [e for e in self.events if isinstance(e, Wait)]

    def phase(self, name):
        return [o for o in self.observations if o.phase == name]


def stop_reason(obs):
    """A Stop if this response means the run must end, else None."""
    if obs.error_kind:
        return Stop("connection", obs.error, obs.n)
    s = obs.status
    if obs.ok or s == 429:
        return None
    if s in (403, 451):
        return Stop("blocked", f"the server answered {s} instead of 429", obs.n)
    if s == 503 and obs.info.retry_after is not None:
        return Stop("overload-503", "the server answered 503 with Retry-After", obs.n, obs.info.retry_after)
    if s >= 500:
        return Stop("server-error", f"the server answered {s}", obs.n)
    if 300 <= s < 400:
        return Stop("redirect", f"the server redirected ({s}); redirects are never followed", obs.n)
    return Stop("status", f"the server answered {s}, which is neither success nor 429", obs.n)


class _Run:
    def __init__(self, transport, budget, clock):
        self.transport, self.budget, self.clock = transport, budget, clock
        self.start = None
        self.events = []

    def elapsed(self):
        return self.clock.now() - self.start

    def send(self, phase):
        r = self.transport.send()  # raises LimitExceeded when the budget is spent
        if self.start is None:
            self.start = r.at
        obs = Observation(
            n=len([e for e in self.events if isinstance(e, Observation)]) + 1,
            phase=phase,
            at=r.at - self.start,
            status=r.status,
            info=rate_headers.parse(list(r.headers), r.wall),
            error_kind=r.error_kind,
            error=r.error,
        )
        self.events.append(obs)
        stop = stop_reason(obs)
        if stop:
            raise _Stopped(stop)
        return obs

    def wait(self, seconds, source, purpose):
        try:
            self.budget.wait(seconds)
        except LimitExceeded as e:
            raise _Stopped(Stop("wait-too-long", f"{source} asked for {e}", retry_after=seconds)) from None
        self.events.append(Wait(self.elapsed() - seconds, seconds, source, purpose))


class _Stopped(Exception):
    def __init__(self, stop):
        self.stop = stop


def _discover(run, cap):
    burst = 0
    while burst < cap:
        obs = run.send("discover")
        if obs.limited:
            return obs, burst
        burst += 1
    return None, burst


def _honour(run, last):
    """Wait as told and retry until accepted. Returns the accepted Observation or None."""
    backoff = BACKOFF_FIRST
    for _ in range(MAX_HONOUR_ATTEMPTS):
        hint, source = last.info.hint()
        if hint is None:
            run.wait(backoff, "back-off", "honour")
            backoff *= 2
        else:
            run.wait(hint, source, "honour")
        obs = run.send("honour")
        if obs.ok:
            return obs
        last = obs
    return None


def _refill(run, burst):
    """Send until the next 429, at most a little past the first burst."""
    accepted = 1  # the request that got back in
    while accepted < burst + 2:
        if run.send("refill").limited:
            return
        accepted += 1


def run_probe(transport, budget, clock, target=None, limits=None, method="GET"):
    run = _Run(transport, budget, clock)
    result = ProbeResult(target, limits, method, run.events)
    began = clock.now()
    reserve = min(RESERVE, budget.cap // 2)
    try:
        first_429, burst = _discover(run, max(1, budget.cap - reserve))
        if first_429 is not None:
            last = first_429
            hint, _ = first_429.info.hint()
            if hint is None or hint >= 1:
                run.wait(hint / 2 if hint else EARLY_WITHOUT_HINT, "early", "early")
                early = run.send("early")
                last = early
            else:
                early = None
            back = early if early is not None and early.ok else _honour(run, last)
            if back is not None:
                _refill(run, burst)
    except _Stopped as s:
        result.stop = s.stop
    except LimitExceeded as e:
        result.stop = Stop("budget", str(e))
    finally:
        transport.close()
    result.duration = clock.now() - began
    return result
