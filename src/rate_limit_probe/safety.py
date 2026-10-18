"""Every limit the probe works within, in one place.

These are not defaults to be tuned per run. The ceilings are constants with
no flag to raise them, Limits refuses to hold a value above them, and the
Budget clamps again, so no path through the code can send more requests,
faster, or wait longer than this file allows.
"""

import dataclasses
import ipaddress
import math
import re
import urllib.parse

MAX_REQUESTS_DEFAULT = 100
MAX_REQUESTS_CEILING = 500
RATE_DEFAULT = 10.0  # requests per second
RATE_CEILING = 10.0  # the rate can be lowered, never raised
MAX_WAIT_DEFAULT = 60.0  # longest single wait, seconds
MAX_WAIT_CEILING = 300.0
REQUEST_TIMEOUT = 10.0  # seconds per request

USER_AGENT_PRODUCT = "rate-limit-probe"

# Headers the probe sets itself, or that would disguise who is asking.
FORBIDDEN_HEADERS = frozenset({
    "host", "user-agent", "content-length", "transfer-encoding", "connection",
    "forwarded", "x-forwarded-for", "x-forwarded-host", "x-real-ip", "x-client-ip",
    "x-originating-ip", "x-remote-ip", "x-remote-addr", "true-client-ip", "cf-connecting-ip",
    "x-cluster-client-ip", "client-ip",
})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_HOSTNAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?)*$")


class LimitExceeded(RuntimeError):
    """A request or wait the limits do not allow. The probe stops cleanly."""


class TargetRefused(ValueError):
    """A URL the probe will not send requests to."""


# --- Request, rate and wait limits ---------------------------------------------

@dataclasses.dataclass(frozen=True)
class Limits:
    max_requests: int = MAX_REQUESTS_DEFAULT
    rate: float = RATE_DEFAULT
    max_wait: float = MAX_WAIT_DEFAULT
    notes: tuple = ()  # what was clamped, for the report

    def __post_init__(self):
        if not 1 <= self.max_requests <= MAX_REQUESTS_CEILING:
            raise ValueError(f"max_requests must be 1 to {MAX_REQUESTS_CEILING}")
        if not 0 < self.rate <= RATE_CEILING:
            raise ValueError(f"rate must be above 0 and at most {RATE_CEILING:g}/s")
        if not 0 < self.max_wait <= MAX_WAIT_CEILING:
            raise ValueError(f"max_wait must be above 0 and at most {MAX_WAIT_CEILING:g} s")


def _positive(name, value):
    if value is None:
        return None
    value = float(value)
    if math.isnan(value) or value <= 0:
        raise ValueError(f"{name} must be above 0")
    return value


def make_limits(max_requests=None, rate=None, max_wait=None):
    """Limits from user input: anything above a ceiling is lowered to it, and
    the report says so."""
    notes = []
    n = _positive("--max-requests", max_requests)
    n = MAX_REQUESTS_DEFAULT if n is None else int(n)
    if n > MAX_REQUESTS_CEILING:
        notes.append(f"--max-requests {n} lowered to the ceiling of {MAX_REQUESTS_CEILING}")
        n = MAX_REQUESTS_CEILING
    r = _positive("--rate", rate)
    r = RATE_DEFAULT if r is None else r
    if r > RATE_CEILING:
        notes.append(f"--rate {r:g}/s lowered to the ceiling of {RATE_CEILING:g}/s")
        r = RATE_CEILING
    w = _positive("--max-wait", max_wait)
    w = MAX_WAIT_DEFAULT if w is None else w
    if w > MAX_WAIT_CEILING:
        notes.append(f"--max-wait {w:g} s lowered to the ceiling of {MAX_WAIT_CEILING:g} s")
        w = MAX_WAIT_CEILING
    return Limits(max(1, n), r, w, tuple(notes))


class Budget:
    """The only way to get permission to send a request or to wait.

    spend() is called immediately before every request: it refuses once the
    budget is used up and sleeps as needed to keep under the rate."""

    def __init__(self, limits, clock):
        if not isinstance(limits, Limits):
            raise TypeError("Budget needs a Limits")
        self.limits = limits
        self.clock = clock
        self.cap = min(limits.max_requests, MAX_REQUESTS_CEILING)
        self.interval = 1.0 / min(limits.rate, RATE_CEILING)
        self.max_wait = min(limits.max_wait, MAX_WAIT_CEILING)
        self.sent = 0
        self._last = None

    @property
    def left(self):
        return self.cap - self.sent

    def spend(self):
        if self.sent >= self.cap:
            raise LimitExceeded(f"request budget of {self.cap} used up")
        if self._last is not None:
            gap = self._last + self.interval - self.clock.now()
            if gap > 0:
                self.clock.sleep(gap)
        self._last = self.clock.now()
        self.sent += 1

    def wait(self, seconds):
        if seconds > self.max_wait:
            raise LimitExceeded(f"a {seconds:g} s wait is longer than the {self.max_wait:g} s allowed")
        self.clock.sleep(seconds)


# --- Targets ---------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Target:
    scheme: str
    host: str  # lower case, no trailing dot, no brackets
    port: int
    path: str  # path and query, as sent
    is_local: bool

    @property
    def netloc(self):
        host = f"[{self.host}]" if ":" in self.host else self.host
        default = {"http": 80, "https": 443}[self.scheme]
        return host if self.port == default else f"{host}:{self.port}"

    @property
    def url(self):
        return f"{self.scheme}://{self.netloc}{self.path}"


def is_local_host(host):
    """localhost by name, or a loopback address. Judged on the name as written,
    never on what it resolves to."""
    if host == "localhost":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    mapped = getattr(ip, "ipv4_mapped", None)
    return ip.is_loopback or (mapped is not None and mapped.is_loopback)


def parse_target(url, authorised=False):
    """A Target, or TargetRefused saying why not."""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise TargetRefused("the URL contains spaces or control characters")
    parts = urllib.parse.urlsplit(url)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise TargetRefused("only http:// and https:// URLs can be probed")
    if "@" in parts.netloc:
        raise TargetRefused("the URL contains a user name or password; send credentials with --header instead")
    if "\\" in parts.netloc:
        raise TargetRefused("the URL's host part contains a backslash")
    host = (parts.hostname or "").rstrip(".")
    if not host:
        raise TargetRefused("the URL has no host")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not _HOSTNAME.match(host):
            raise TargetRefused(f"{host!r} is not a valid host name") from None
    try:
        port = parts.port
    except ValueError:
        raise TargetRefused("the URL's port is not valid") from None
    port = port or {"http": 80, "https": 443}[scheme]
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    local = is_local_host(host)
    if not local and not authorised:
        raise TargetRefused(
            f"{host} is not a local address. Only localhost and the bundled test server are probed by "
            "default. If you own this API or are authorised to test it, add --i-am-authorised."
        )
    return Target(scheme, host, port, path, local)


def authorised_warning(target, limits):
    return (
        f"WARNING: {target.host} is not a local address. Only probe APIs you own or are authorised to test.\n"
        f"         This run sends up to {limits.max_requests} requests, at most {limits.rate:g} per second, "
        "on one connection,\n"
        "         and stops at the first sign of a block. Every request uses up the API key's own quota,\n"
        "         so use a test key or a test environment, not the customer's production key."
    )


def check_extra_headers(values):
    """[(name, value)] from "Name: value" strings, refusing any header that the
    probe sets itself or that would disguise the client."""
    out = []
    for raw in values:
        name, sep, value = raw.partition(":")
        name, value = name.strip(), value.strip()
        if not sep or not _HEADER_NAME.match(name):
            raise ValueError("--header must look like 'Name: value'")
        if any(ord(c) < 32 and c != "\t" for c in value):
            raise ValueError(f"--header {name} contains control characters")
        if name.lower() in FORBIDDEN_HEADERS:
            raise ValueError(f"--header {name} is not allowed: the probe always identifies itself honestly")
        out.append((name, value))
    return out
