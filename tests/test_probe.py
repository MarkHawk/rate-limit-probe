import pytest

from rate_limit_probe.probe import Observation, Wait
from rate_limit_probe.safety import make_limits
from rate_limit_probe.server.scenarios import Scenario
from probe_helpers import probe_scenario


def shape(result):
    """A compact picture of the run: phase:status per request, and waits."""
    out = []
    for e in result.events:
        if isinstance(e, Observation):
            out.append(f"{e.phase}:{e.status}")
        else:
            out.append(f"wait {e.seconds:g} {e.source}")
    return out


def test_fixed_window_happy_path():
    r = probe_scenario("fixed-seconds")
    assert r.stop is None
    assert shape(r) == (
        ["discover:200"] * 5 + ["discover:429", "wait 5 early", "early:429", "wait 5 Retry-After", "honour:200"]
        + ["refill:200"] * 4 + ["refill:429"]
    )
    assert r.duration == pytest.approx(11.0)


def test_penalty_early_retry_extends_the_wait():
    r = probe_scenario("penalty")
    early = r.phase("early")[0]
    assert early.limited and early.info.retry_after == 10
    assert [w.seconds for w in r.waits] == [5, 10]
    assert r.phase("honour")[0].ok


def test_lying_needs_several_honoured_waits():
    r = probe_scenario("lying")
    honours = r.phase("honour")
    assert [o.status for o in honours][-1] == 200
    assert len(honours) > 2 and all(o.limited for o in honours[:-1])


def test_no_headers_backs_off_by_doubling():
    r = probe_scenario("none")
    assert [(w.seconds, w.source) for w in r.waits] == [(1, "early"), (1, "back-off"), (2, "back-off"),
                                                        (4, "back-off"), (8, "back-off")]
    assert r.phase("honour")[-1].ok


def test_x_ratelimit_reset_is_used_when_there_is_no_retry_after():
    r = probe_scenario("xrl")
    assert {w.source for w in r.waits} == {"early", "X-RateLimit-Reset"}
    assert r.phase("honour")[-1].ok


def test_token_bucket_refills_gradually():
    r = probe_scenario("bucket-ietf")
    assert r.stop is None
    assert len([o for o in r.phase("refill") if o.ok]) == 0  # only the honour request got in


def test_block_stops_the_run():
    r = probe_scenario("block")
    assert r.stop.kind == "blocked" and "403" in r.stop.detail
    assert r.observations[-1].status == 403


def test_overload_503_stops_with_its_wait():
    r = probe_scenario("overload-503")
    assert r.stop.kind == "overload-503" and r.stop.retry_after == 10
    assert len(r.observations) == 6


def test_no_limit_within_the_budget():
    r = probe_scenario(Scenario("big", "big", limit=1000), limits=make_limits(max_requests=30))
    assert r.stop is None
    assert len(r.observations) == 15 and all(o.ok for o in r.observations)


def test_wait_longer_than_allowed_stops():
    r = probe_scenario(Scenario("slow", "slow", window=3600))
    assert r.stop.kind == "wait-too-long"
    assert not [w for w in r.waits if w.purpose == "honour"]


def test_budget_running_out_stops_cleanly():
    # 3 for discovery, 3 kept back: the 7th request is never sent
    r = probe_scenario(Scenario("l", "l", limit=2, retry_after_scale=0.2), limits=make_limits(max_requests=6))
    assert r.stop.kind == "budget" and len(r.observations) == 6


def test_never_more_than_the_budget_or_faster_than_the_rate():
    for name in ("fixed-seconds", "none", "lying", "penalty", "bucket-ietf"):
        r = probe_scenario(name, limits=make_limits(max_requests=12, rate=4))
        times = [o.at for o in r.observations]
        assert len(times) <= 12
        assert all(b - a >= 0.25 - 1e-9 for a, b in zip(times, times[1:]))


def test_redirect_and_other_statuses_stop(monkeypatch):
    from rate_limit_probe.server import scenarios

    for status, kind in ((302, "redirect"), (401, "status"), (500, "server-error"), (451, "blocked")):
        monkeypatch.setattr(scenarios.Endpoint, "respond", lambda self, now, wall, s=status: (s, [], b""))
        r = probe_scenario("fixed-seconds")
        assert r.stop.kind == kind and len(r.observations) == 1
