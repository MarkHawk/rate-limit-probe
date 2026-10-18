"""The HTTP test server. It binds to 127.0.0.1 only: there is no option to
listen anywhere else."""

import http.server
import threading

from rate_limit_probe.clock import RealClock
from rate_limit_probe.server.scenarios import SCENARIOS, Endpoint

BIND_ADDRESS = "127.0.0.1"


def _handler(server_state):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        server_version = "rate-limit-probe-test-server"
        sys_version = ""

        def date_time_string(self, timestamp=None):
            # The Date header follows the shared clock, so Retry-After dates
            # stay consistent with it under a fake clock.
            return super().date_time_string(server_state.clock.wall() if timestamp is None else timestamp)

        def log_message(self, format, *args):
            if server_state.log is not None:
                server_state.log(f"{self.command} {self.path} -> " + (args[1] if len(args) > 1 else ""))

        def _serve(self, send_body):
            path = self.path.split("?", 1)[0]
            endpoint = server_state.endpoints.get(path)
            if endpoint is None:
                status, headers, body = 404, [], ("not found; try: " + " ".join(sorted(server_state.endpoints)) + "\n").encode()
            else:
                with server_state.lock:
                    status, headers, body = endpoint.respond(server_state.clock.now(), server_state.clock.wall())
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if send_body:
                self.wfile.write(body)

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):
            self._serve(False)

    return Handler


class _State:
    def __init__(self, endpoints, clock, log):
        self.endpoints, self.clock, self.log = endpoints, clock, log
        self.lock = threading.Lock()


class _HTTPServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request, client_address):
        pass  # a client hanging up, or refusing our test certificate, is not news


class TestServer:
    """Run endpoints on 127.0.0.1 in a background thread.

    endpoints maps a path to an Endpoint. ssl_context, if given, is a server
    context (the HTTPS tests use a throwaway CA)."""

    __test__ = False  # not a pytest test class

    def __init__(self, endpoints, *, port=0, clock=None, ssl_context=None, log=None):
        self.state = _State(dict(endpoints), clock or RealClock(), log)
        self.httpd = _HTTPServer((BIND_ADDRESS, port), _handler(self.state))
        self.scheme = "http"
        if ssl_context is not None:
            self.httpd.socket = ssl_context.wrap_socket(self.httpd.socket, server_side=True)
            self.scheme = "https"
        self.thread = None

    @property
    def port(self):
        return self.httpd.server_address[1]

    def url(self, path="/", host=BIND_ADDRESS):
        return f"{self.scheme}://{host}:{self.port}{path}"

    def start(self):
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, name="rate-limit-probe-server", daemon=True,
        )
        self.thread.start()
        return self

    def serve_forever(self):
        self.httpd.serve_forever()

    def close(self):
        if self.thread is not None:
            self.httpd.shutdown()
            self.thread.join(timeout=5)
        self.httpd.server_close()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.close()


def scenario_endpoints(scenarios=SCENARIOS):
    """Each scenario at /s/<name>."""
    return {f"/s/{s.name}": Endpoint(s) for s in scenarios}
