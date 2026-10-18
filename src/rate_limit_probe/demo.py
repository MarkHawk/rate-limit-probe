"""`rate-limit-probe demo`: probe every bundled behaviour, one line each.

The test server runs in-process on 127.0.0.1 with a random port, and each
behaviour is probed in turn over the real network stack and the real clock,
exactly as an API would be. Windows are shortened so the whole demo takes
well under a minute; penalty and lying get a little longer, because a
whole-second Retry-After cannot show them in 2 s.
"""

import argparse
import sys

from rate_limit_probe.analyse import analyse
from rate_limit_probe.clock import RealClock
from rate_limit_probe.findings import Status
from rate_limit_probe.probe import run_probe
from rate_limit_probe.render import render_json, render_text, use_colour
from rate_limit_probe.safety import Budget, make_limits, parse_target
from rate_limit_probe.server.app import TestServer
from rate_limit_probe.server.scenarios import BY_NAME, SCENARIOS, Endpoint, scaled
from rate_limit_probe.timefmt import secs
from rate_limit_probe.transport import Transport

DEMO_WINDOW = 2.0
DEMO_WINDOWS = {"penalty": 4.0, "lying": 5.0}

INTRO = "rate-limit-probe demo: {n} behaviours on a local test server, probed one after another"


def demo_scenarios():
    return [scaled(s, DEMO_WINDOWS.get(s.name, DEMO_WINDOW)) for s in SCENARIOS]


def build_parser():
    p = argparse.ArgumentParser(
        prog="rate-limit-probe demo",
        description="Start the test server in-process and probe each behaviour. Local only, no flags needed.",
    )
    p.add_argument("name", nargs="?", choices=[s.name for s in SCENARIOS], metavar="name",
                   help="show the full report for one behaviour: " + ", ".join(s.name for s in SCENARIOS))
    p.add_argument("--json", action="store_true", help="with a name, print its report as JSON")
    return p


def probe_one(server, scenario, clock):
    limits = make_limits()
    target = parse_target(server.url(f"/s/{scenario.name}"))
    budget = Budget(limits, clock)
    result = run_probe(Transport(target, budget, clock), budget, clock, target, limits)
    return analyse(result, notes=[f"demo window shortened to {secs(scenario.window)}"])


def one_line(report):
    """(status, text) for the summary line."""
    fails, warns = report.count(Status.FAIL), report.count(Status.WARN)
    cause = report.likely_cause
    if fails or warns:
        return (Status.FAIL if fails else Status.WARN), f"likely cause: {cause.label}"
    observed = report.finding("limit.observed")
    d = observed.details if observed else {}
    if d.get("shape") == "gradual":
        return Status.PASS, "bursts, then refills gradually; headers consistent"
    if d.get("window_nice"):
        return Status.PASS, f"{d['burst']} per {secs(d['window_nice'])}, headers consistent"
    return Status.PASS, cause.label if cause else "no problems found"


def demo_command(argv, clock=None, stdout=None, stderr=None):
    stdout = stdout or sys.stdout
    args = build_parser().parse_args(argv)
    clock = clock or RealClock()
    scenarios = demo_scenarios()
    if args.name:
        scenarios = [s for s in scenarios if s.name == args.name]
    with TestServer({f"/s/{s.name}": Endpoint(s) for s in scenarios}, clock=clock) as server:
        if args.name:
            report = probe_one(server, scenarios[0], clock)
            stdout.write(render_json(report) if args.json else render_text(report, colour=use_colour(stdout)))
            return report.exit_code
        print(INTRO.format(n=len(scenarios)) + "\n", file=stdout, flush=True)
        name_w = max(len(s.name) for s in scenarios)
        summary_w = max(len(BY_NAME[s.name].summary) for s in scenarios)
        for s in scenarios:
            status, text = one_line(probe_one(server, s, clock))
            print(f"  {s.name:<{name_w}}  {s.summary:<{summary_w}}  {status.value:<4}  {text}", file=stdout, flush=True)
    print("", file=stdout)
    print("Full report for one behaviour:  rate-limit-probe demo <name>   e.g. rate-limit-probe demo penalty",
          file=stdout)
    print("Run the test server yourself:   rate-limit-probe serve", file=stdout)
    print("Probe your own API:             rate-limit-probe <url>", file=stdout)
    return 0
