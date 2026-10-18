"""Send one request at a time over a single connection.

http.client is used directly so nothing happens behind the probe's back: no
retries, no redirects followed, no proxies from the environment, no
connection pool. HTTPS always verifies the certificate and host name; the
only choice is which CA to trust (the system's, or --cacert).
"""

import dataclasses
import http.client
import socket
import ssl

from rate_limit_probe import __version__
from rate_limit_probe.safety import REQUEST_TIMEOUT, USER_AGENT_PRODUCT

USER_AGENT = f"{USER_AGENT_PRODUCT}/{__version__} (diagnostic; one connection, capped request rate)"
METHODS = ("GET", "HEAD")
MAX_BODY = 1 << 20  # read at most 1 MiB of a body; the probe never looks at it
# Reopen the connection after this long idle, so a server's keep-alive
# timeout during a long wait is not mistaken for a block.
IDLE_REOPEN = 4.0


@dataclasses.dataclass(frozen=True)
class Response:
    at: float  # clock.now() when the request was sent
    wall: float  # local Unix time the response (or error) arrived
    status: int | None = None
    headers: tuple = ()  # (name, value) as received
    error_kind: str | None = None  # tls, refused, closed, timeout, dns, network, protocol
    error: str | None = None


def tls_context(cacert=None):
    """Certificate and host name verification on, always."""
    try:
        ctx = ssl.create_default_context(cafile=cacert)
    except (FileNotFoundError, ssl.SSLError, OSError) as e:
        raise ValueError(f"--cacert {cacert}: cannot load it as a PEM CA certificate ({e.__class__.__name__})") from None
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    return ctx


def _classify(exc):
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "tls", f"certificate verification failed: {exc.verify_message or exc.reason}"
    if isinstance(exc, ssl.SSLError):
        return "tls", f"TLS error: {exc.reason or exc}"
    if isinstance(exc, ConnectionRefusedError):
        return "refused", "connection refused"
    if isinstance(exc, (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError, ConnectionAbortedError)):
        return "closed", "the server closed or reset the connection"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout", "no response within the request timeout"
    if isinstance(exc, socket.gaierror):
        return "dns", "the host name could not be resolved"
    if isinstance(exc, http.client.HTTPException):
        return "protocol", f"invalid HTTP response ({exc.__class__.__name__})"
    return "network", f"network error ({exc.__class__.__name__})"


class Transport:
    def __init__(self, target, budget, clock, *, method="GET", extra_headers=(), cacert=None,
                 timeout=REQUEST_TIMEOUT):
        if method not in METHODS:
            raise ValueError(f"method must be one of {', '.join(METHODS)}")
        self.target, self.budget, self.clock, self.method = target, budget, clock, method
        self.headers = {"User-Agent": USER_AGENT, "Accept": "*/*", **dict(extra_headers)}
        self.timeout = min(timeout, REQUEST_TIMEOUT)
        self.context = tls_context(cacert) if target.scheme == "https" else None
        if cacert and target.scheme != "https":
            raise ValueError("--cacert only applies to https:// URLs")
        self.conn = None
        self._last_done = None

    def _connection(self):
        if self.conn is not None and self._last_done is not None and self.clock.now() - self._last_done > IDLE_REOPEN:
            self.close()
        if self.conn is None:
            if self.context is not None:
                self.conn = http.client.HTTPSConnection(
                    self.target.host, self.target.port, timeout=self.timeout, context=self.context)
            else:
                self.conn = http.client.HTTPConnection(self.target.host, self.target.port, timeout=self.timeout)
        return self.conn

    def send(self):
        """One request, after the budget allows it. Raises LimitExceeded when it does not."""
        self.budget.spend()
        at = self.clock.now()
        conn = self._connection()
        try:
            conn.request(self.method, self.target.path, headers=self.headers)
            r = conn.getresponse()
            headers = tuple(r.getheaders())
            r.read(MAX_BODY)
            if not r.isclosed():
                self.close()  # body larger than we read: drop it with the connection
            return Response(at, self.clock.wall(), status=r.status, headers=headers)
        except (OSError, http.client.HTTPException) as e:
            self.close()
            kind, message = _classify(e)
            return Response(at, self.clock.wall(), error_kind=kind, error=message)
        finally:
            self._last_done = self.clock.now()

    def close(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
