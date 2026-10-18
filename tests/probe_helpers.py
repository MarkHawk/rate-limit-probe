import contextlib

from rate_limit_probe.clock import FakeClock
from rate_limit_probe.probe import run_probe
from rate_limit_probe.safety import Budget, make_limits, parse_target
from rate_limit_probe.server.app import TestServer, scenario_endpoints
from rate_limit_probe.server.scenarios import SCENARIOS, Endpoint, Scenario
from rate_limit_probe.transport import Transport


@contextlib.contextmanager
def scenario_server(clock, scenarios=SCENARIOS):
    with TestServer(scenario_endpoints(scenarios), clock=clock) as server:
        yield server


def probe_scenario(name_or_scenario, limits=None, clock=None):
    """Probe one scenario on a fresh server under a fake clock."""
    clock = clock or FakeClock()
    scenario = name_or_scenario if isinstance(name_or_scenario, Scenario) else None
    endpoints = {"/s/x": Endpoint(scenario)} if scenario else scenario_endpoints()
    path = "/s/x" if scenario else f"/s/{name_or_scenario}"
    limits = limits or make_limits()
    with TestServer(endpoints, clock=clock) as server:
        target = parse_target(server.url(path))
        budget = Budget(limits, clock)
        return run_probe(Transport(target, budget, clock), budget, clock, target, limits)
