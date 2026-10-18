"""Command line entry point.

    rate-limit-probe <url> [options]      probe a URL (localhost unless --i-am-authorised)
"""

import argparse
import sys

from rate_limit_probe import __version__, safety
from rate_limit_probe.analyse import analyse
from rate_limit_probe.clock import RealClock
from rate_limit_probe.probe import run_probe
from rate_limit_probe.render import limits_line, render_text, use_colour
from rate_limit_probe.redact import display_url
from rate_limit_probe.transport import METHODS, Transport


def build_parser():
    p = argparse.ArgumentParser(
        prog="rate-limit-probe",
        description=(
            "Send a small, capped sequence of requests to an API you own or are authorised to test, and explain "
            "in plain English how its rate limiting behaves and why a client keeps getting 429 Too Many Requests."
        ),
        epilog="Only localhost is probed by default. Every request uses the API key's own quota.",
    )
    p.add_argument("url", help="the URL to probe, http:// or https://")
    p.add_argument("--max-requests", type=float, metavar="N",
                   help=f"most requests to send (default {safety.MAX_REQUESTS_DEFAULT}, "
                        f"at most {safety.MAX_REQUESTS_CEILING})")
    p.add_argument("--rate", type=float, metavar="PER_SECOND",
                   help=f"most requests per second (default and ceiling {safety.RATE_CEILING:g}); lower only")
    p.add_argument("--max-wait", type=float, metavar="SECONDS",
                   help=f"longest single wait the probe will make (default {safety.MAX_WAIT_DEFAULT:g}, "
                        f"at most {safety.MAX_WAIT_CEILING:g})")
    p.add_argument("--method", choices=METHODS, default="GET", help="GET (default) or HEAD; nothing that changes data")
    p.add_argument("--header", action="append", default=[], metavar="'NAME: VALUE'",
                   help="send this header on every request, e.g. a test key; never shown in the report")
    p.add_argument("--cacert", metavar="FILE", help="trust this PEM CA certificate for https:// (a private CA)")
    p.add_argument("--i-am-authorised", action="store_true",
                   help="allow a host other than localhost: you own the API or are authorised to test it")
    p.add_argument("--version", action="version", version=f"rate-limit-probe {__version__}")
    return p


def probe_command(argv, clock=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    parser = build_parser()
    args = parser.parse_args(argv)
    clock = clock or RealClock()

    def fail(message):
        print(f"rate-limit-probe: {message}", file=stderr)
        return 2

    try:
        limits = safety.make_limits(args.max_requests, args.rate, args.max_wait)
        target = safety.parse_target(args.url, authorised=args.i_am_authorised)
        extra = safety.check_extra_headers(args.header)
        budget = safety.Budget(limits, clock)
        transport = Transport(target, budget, clock, method=args.method, extra_headers=extra, cacert=args.cacert)
    except (ValueError, safety.TargetRefused) as e:
        return fail(str(e))

    if not target.is_local:
        print(safety.authorised_warning(target, limits), file=stderr)
    print(f"Probing {display_url(target)}: {limits_line(limits)}. This can take a minute.", file=stderr)

    result = run_probe(transport, budget, clock, target, limits, args.method)
    report = analyse(result, notes=limits.notes)
    stdout.write(render_text(report, colour=use_colour(stdout)))
    return report.exit_code


def main(argv=None, **kwargs):
    argv = sys.argv[1:] if argv is None else list(argv)
    return probe_command(argv, **kwargs)
