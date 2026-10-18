"""`rate-limit-probe serve`: run the test server in the foreground.

Every scenario is served at /s/<name>. --algorithm adds a custom endpoint at
/custom built from the other flags. The server listens on 127.0.0.1 only.
"""

import argparse
import sys

from rate_limit_probe.server import emit
from rate_limit_probe.server.app import TestServer
from rate_limit_probe.server.limiters import ALGORITHMS
from rate_limit_probe.server.scenarios import SCENARIOS, Endpoint, Scenario, scaled

DEFAULT_PORT = 8429


def build_parser():
    p = argparse.ArgumentParser(
        prog="rate-limit-probe serve",
        description="Run the bundled rate-limit test server on 127.0.0.1, for practice or to reproduce a ticket.",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port on 127.0.0.1 (default {DEFAULT_PORT})")
    p.add_argument("--window", type=float, metavar="SECONDS",
                   help="use this window for every endpoint instead of each scenario's own (10 s)")
    p.add_argument("--algorithm", choices=sorted(ALGORITHMS), help="also serve a custom endpoint at /custom")
    p.add_argument("--limit", type=int, default=5, help="requests per window for /custom (default 5)")
    p.add_argument("--headers", default="retry-after-seconds", metavar="STYLES",
                   help="comma-separated header styles for /custom: " + ", ".join(emit.STYLES))
    p.add_argument("--penalty", action="store_true", help="/custom restarts the limit on every early request")
    p.add_argument("--quiet", action="store_true", help="do not log each request")
    return p


def endpoints_from(args):
    scenarios = [scaled(s, args.window) if args.window else s for s in SCENARIOS]
    endpoints = {f"/s/{s.name}": Endpoint(s) for s in scenarios}
    if args.algorithm:
        custom = Scenario(
            "custom", "custom", algorithm=args.algorithm, limit=args.limit, window=args.window or 10.0,
            headers=tuple(h.strip() for h in args.headers.split(",") if h.strip()), penalty=args.penalty,
        )
        endpoints["/custom"] = Endpoint(custom)
    return endpoints


def serve_command(argv, stdout=None, stderr=None, ready=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    args = build_parser().parse_args(argv)
    try:
        endpoints = endpoints_from(args)
        log = None if args.quiet else (lambda line: print(line, file=stderr, flush=True))
        server = TestServer(endpoints, port=args.port, log=log)
    except ValueError as e:
        print(f"rate-limit-probe serve: {e}", file=stderr)
        return 2
    except OSError as e:
        print(f"rate-limit-probe serve: cannot listen on 127.0.0.1:{args.port} ({e.strerror})", file=stderr)
        return 2
    print(f"rate-limit-probe test server on {server.url('/')} (127.0.0.1 only). Ctrl+C to stop.", file=stdout)
    summaries = {f"/s/{s.name}": s.summary for s in SCENARIOS}
    summaries["/custom"] = "custom endpoint from the command line"
    width = max(len(server.url(path)) for path in endpoints)
    for path in endpoints:
        print(f"  {server.url(path):<{width}}  {summaries[path]}", file=stdout)
    print(f"\nTry:  rate-limit-probe {server.url('/s/penalty')}", file=stdout, flush=True)
    if ready:
        ready(server)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.httpd.server_close()
    return 0
