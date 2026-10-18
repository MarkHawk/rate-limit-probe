import pytest

from rate_limit_probe.server.limiters import Block, FixedWindow, Penalty, SlidingWindow, TokenBucket


def run(limiter, times):
    return [limiter.check(t) for t in times]


def test_fixed_window_allows_limit_then_resets_at_the_boundary():
    lim = FixedWindow(3, 10)
    d = run(lim, [0, 1, 2, 3])
    assert [x.allowed for x in d] == [True, True, True, False]
    assert [x.remaining for x in d[:3]] == [2, 1, 0]
    assert d[3].retry_after == pytest.approx(7)
    assert lim.check(9.9).allowed is False
    after = lim.check(10)
    assert after.allowed and after.remaining == 2


def test_rejected_requests_do_not_consume_quota():
    lim = FixedWindow(1, 10)
    lim.check(0)
    for t in range(1, 10):
        assert not lim.check(t).allowed
    assert lim.check(10).allowed


def test_sliding_window_frees_one_slot_at_a_time():
    lim = SlidingWindow(3, 10)
    run(lim, [0, 1, 2])
    refused = lim.check(5)
    assert not refused.allowed and refused.retry_after == pytest.approx(5)
    assert lim.check(10).allowed  # the request at 0 has expired
    assert not lim.check(10.5).allowed  # the one at 1 has not
    assert lim.check(11).allowed


def test_token_bucket_bursts_then_refills_gradually():
    lim = TokenBucket(4, 8)  # 0.5 tokens per second
    assert all(d.allowed for d in run(lim, [0, 0, 0, 0]))
    refused = lim.check(0)
    assert not refused.allowed and refused.retry_after == pytest.approx(2)
    assert refused.reset_after == pytest.approx(8)
    assert lim.check(2).allowed
    assert not lim.check(2.1).allowed


def test_penalty_restarts_the_lockout_on_every_early_request():
    lim = Penalty(FixedWindow(2, 10), lockout=10)
    run(lim, [0, 1])
    first = lim.check(2)
    assert not first.allowed and first.retry_after == 10
    early = lim.check(7)  # 5 s early: lockout now ends at 17
    assert not early.allowed and early.retry_after == 10
    assert not lim.check(16).allowed  # and now at 26
    assert lim.check(26).allowed


def test_penalty_lets_a_patient_client_back_in():
    lim = Penalty(FixedWindow(2, 10), lockout=10)
    run(lim, [0, 1, 2])
    assert lim.check(12).allowed


def test_block_answers_403_after_repeated_refusals():
    lim = Block(FixedWindow(1, 10), block_after=2)
    d = run(lim, [0, 1, 2, 3, 20])
    assert [x.allowed for x in d] == [True, False, False, False, False]
    assert [x.status for x in d] == [None, None, None, 403, 403]
