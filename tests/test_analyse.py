import pytest

from rate_limit_probe.analyse import analyse
from rate_limit_probe.findings import Status
from rate_limit_probe.safety import make_limits
from rate_limit_probe.server.scenarios import Scenario
from probe_helpers import probe_scenario


def report(name, **kwargs):
    return analyse(probe_scenario(name, **kwargs))


def statuses(r):
    return {f.check_id: f.status for f in r.findings}


def test_healthy_fixed_window():
    r = report("fixed-seconds")
    s = statuses(r)
    assert s["headers.retry_after"] == Status.PASS
    assert s["behaviour.early_retry"] == Status.PASS
    assert s["behaviour.recovery"] == Status.PASS
    assert r.count(Status.FAIL) == 0 and r.exit_code == 0
    assert r.finding("limit.start").details == {"request": 6, "at": pytest.approx(0.5), "burst": 5}
    observed = r.finding("limit.observed")
    assert observed.details["shape"] == "full" and observed.details["window"] == pytest.approx(10.5)
    assert observed.details["window_nice"] == 10
    assert "refused until 10.5 s after the first request" in observed.summary and "5 per 10 s" in observed.summary


def test_penalty():
    r = report("penalty")
    f = r.finding("behaviour.early_retry")
    assert f.status == Status.FAIL and f.details["penalised"] is True
    assert r.finding("headers.quota").status == Status.PASS
    assert r.exit_code == 1


def test_lying():
    r = report("lying")
    f = r.finding("headers.retry_after")
    assert f.status == Status.FAIL and f.details["too_short"] is True
    assert f.details["said"] == 2 and f.details["actual"] > 8
    assert r.finding("behaviour.early_retry").status == Status.PASS


def test_stale_remaining():
    f = report("stale-remaining").finding("headers.quota")
    assert f.status == Status.FAIL
    assert "did not count down" in f.summary and "still said 4 left when the next request was refused" in f.summary


def test_no_headers():
    r = report("none")
    assert r.finding("headers.retry_after").status == Status.WARN
    assert r.finding("headers.quota").status == Status.INFO
    assert r.finding("behaviour.early_retry").details["penalised"] is None
    assert r.finding("limit.observed").details["window"] is not None
    assert "between" in r.finding("limit.observed").summary
    assert r.exit_code == 0


def test_x_ratelimit_only():
    r = report("xrl")
    assert r.finding("headers.retry_after").status == Status.WARN
    assert "X-RateLimit-Reset" in r.finding("headers.retry_after").summary
    assert r.finding("headers.quota").status == Status.PASS


def test_token_bucket_is_gradual():
    r = report("bucket-ietf")
    f = r.finding("limit.observed")
    assert f.details["shape"] == "gradual" and "token bucket" in f.summary
    assert r.finding("headers.quota").title == "RateLimit headers"
    assert r.exit_code == 0


def test_block_and_503():
    blocked = report("block").finding("run.stopped")
    assert blocked.status == Status.FAIL and blocked.details["kind"] == "blocked"
    overload = report("overload-503").finding("run.stopped")
    assert "503 instead of 429" in overload.summary and "10 s" in overload.summary


def test_no_limit():
    r = report(Scenario("big", "big", limit=1000), limits=make_limits(max_requests=20))
    assert statuses(r)["limit.none"] == Status.INFO and r.exit_code == 0


def test_every_scenario_analyses_without_error():
    from rate_limit_probe.server.scenarios import SCENARIOS
    for s in SCENARIOS:
        assert report(s.name).findings


def test_penalty_window_is_not_inflated_by_the_early_retry():
    assert report("penalty").finding("limit.observed").details["window"] == pytest.approx(10.5)


def test_block_does_not_blame_retry_after():
    r = report("block")
    assert r.finding("headers.retry_after").status == Status.PASS
    assert r.finding("behaviour.recovery") is None


def test_503_says_when_limiting_started():
    r = report("overload-503")
    assert r.finding("limit.none") is None
    assert "answered 503 (not 429)" in r.finding("limit.start").summary


def test_lying_recovery_is_a_warning():
    f = report("lying").finding("behaviour.recovery")
    assert f.status == Status.WARN and "7th try" in f.summary


def test_window_quoted_rounded_down_on_a_slightly_late_clock():
    """On a real clock every sleep overruns a little, so the reset is seen just
    past 10.5 s. The window must still read as 10 s, not 11."""
    from rate_limit_probe.clock import FakeClock

    class LateClock(FakeClock):
        def sleep(self, seconds):
            super().sleep(seconds + 0.004)

    for name in ("lying", "penalty", "fixed-seconds"):
        f = analyse(probe_scenario(name, clock=LateClock())).finding("limit.observed")
        assert f.details["window"] > 10.5 and f.details["window_nice"] == 10, name
        assert "5 per 10 s" in f.summary


def test_a_fast_refilling_bucket_is_not_a_header_fault():
    """Remaining 0 then another acceptance, and more than Limit accepted, are
    what a token bucket does when it refills during the burst."""
    from rate_limit_probe.server.scenarios import BY_NAME, scaled
    r = analyse(probe_scenario(scaled(BY_NAME["bucket-ietf"], 2)))
    assert r.finding("headers.quota").status == Status.PASS
    assert r.finding("headers.retry_after").status == Status.PASS
    assert r.exit_code == 0
