import pytest

from rate_limit_probe.analyse import analyse
from rate_limit_probe.causes import likely_cause
from rate_limit_probe.safety import make_limits
from rate_limit_probe.server.scenarios import SCENARIOS, Scenario
from probe_helpers import probe_scenario


def cause(name, **kwargs):
    return likely_cause(analyse(probe_scenario(name, **kwargs)))


@pytest.mark.parametrize("name, pattern", [
    ("fixed-seconds", "server-behaves"),
    ("sliding-date", "server-behaves"),
    ("bucket-ietf", "server-behaves"),
    ("xrl", "retry-after-missing"),
    ("none", "no-headers"),
    ("lying", "retry-after-too-short"),
    ("stale-remaining", "quota-headers-wrong"),
    ("penalty", "early-retries-extend"),
    ("block", "blocked"),
    ("overload-503", "503-as-limit"),
])
def test_each_scenario_has_its_cause(name, pattern):
    assert cause(name).pattern == pattern


def test_every_scenario_is_covered():
    assert {s.name for s in SCENARIOS} == {
        "fixed-seconds", "sliding-date", "bucket-ietf", "xrl", "none", "lying",
        "stale-remaining", "penalty", "block", "overload-503"}


def test_penalty_wording_matches_the_ticket():
    c = cause("penalty")
    assert "retries immediately and ignores Retry-After never leaves the limit" in c.summary
    assert "10 s" in c.next_step


def test_lying_names_both_waits():
    c = cause("lying")
    assert "(2 s)" in c.summary and "about 10 s" in c.summary


def test_no_headers_gives_a_back_off_floor():
    c = cause("none")
    assert "at least 16 s" in c.next_step


def test_503_cause_has_the_wait():
    assert "10 s" in cause("overload-503").summary


def test_limit_not_reached():
    c = cause(Scenario("big", "big", limit=1000), limits=make_limits(max_requests=20))
    assert c.pattern == "limit-not-reached"


def test_long_lockout_and_budget():
    assert cause(Scenario("slow", "slow", window=3600)).pattern == "long-lockout"
    assert cause(Scenario("l", "l", limit=2, retry_after_scale=0.2),
                 limits=make_limits(max_requests=6)).pattern == "incomplete"


def test_analyse_attaches_the_cause():
    assert analyse(probe_scenario("penalty")).likely_cause.pattern == "early-retries-extend"
