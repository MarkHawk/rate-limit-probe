import email.utils

import pytest

from rate_limit_probe.headers import parse, parse_sf_list

NOW = 1_790_000_000.0
DATE = email.utils.formatdate(NOW, usegmt=True)


def p(*pairs, received=NOW):
    return parse(list(pairs), received)


def test_nothing():
    info = p(("Content-Type", "text/plain"))
    assert info.styles == () and info.hint() == (None, None) and info.raw == ()


def test_retry_after_seconds():
    info = p(("Retry-After", "7"))
    assert (info.retry_after, info.retry_after_form) == (7, "seconds")
    assert info.hint() == (7, "Retry-After")
    assert info.styles == ("retry-after-seconds",)


def test_retry_after_date_uses_the_date_header_not_the_local_clock():
    later = email.utils.formatdate(NOW + 30, usegmt=True)
    info = p(("Date", DATE), ("Retry-After", later), received=NOW + 500)  # local clock 500 s fast
    assert info.retry_after == 30 and info.retry_after_form == "date"


def test_retry_after_date_without_a_date_header_uses_arrival_time():
    info = p(("Retry-After", email.utils.formatdate(NOW + 12, usegmt=True)))
    assert info.retry_after == 12


@pytest.mark.parametrize("value, problem", [
    ("soon", "neither whole seconds nor an HTTP date"),
    ("1.5", "has a fraction"),
])
def test_retry_after_problems(value, problem):
    assert problem in p(("Retry-After", value)).problems[0]


def test_retry_after_in_the_past():
    info = p(("Date", DATE), ("Retry-After", email.utils.formatdate(NOW - 60, usegmt=True)))
    assert info.retry_after == 0 and "in the past" in info.problems[0]


def test_x_ratelimit_with_epoch_reset():
    info = p(("Date", DATE), ("X-RateLimit-Limit", "5"), ("X-RateLimit-Remaining", "0"),
             ("X-RateLimit-Reset", str(int(NOW) + 8)))
    assert (info.limit, info.remaining, info.reset) == (5, 0, 8)
    assert info.hint() == (8, "X-RateLimit-Reset")


def test_x_ratelimit_with_delta_and_millisecond_epoch():
    assert p(("X-RateLimit-Reset", "8")).reset == 8
    assert p(("Date", DATE), ("X-RateLimit-Reset", str(int(NOW * 1000) + 3000))).reset == pytest.approx(3)
    assert p(("x-rate-limit-remaining", "2")).remaining == 2


def test_reset_is_only_a_hint_when_nothing_is_left():
    assert p(("X-RateLimit-Remaining", "3"), ("X-RateLimit-Reset", "8")).hint() == (None, None)


def test_x_ratelimit_problems():
    info = p(("X-RateLimit-Remaining", "lots"), ("X-RateLimit-Reset", "never"))
    assert len(info.problems) == 2


def test_ietf_structured_fields():
    info = p(("RateLimit-Policy", '"default";q=100;w=60'), ("RateLimit", '"default";r=0;t=12'))
    assert (info.limit, info.remaining, info.reset, info.window) == (100, 0, 12, 60)
    assert info.styles == ("ietf",)
    assert info.hint() == (12, "RateLimit")


def test_ietf_picks_the_matching_policy():
    info = p(("RateLimit-Policy", '"burst";q=10;w=1, "daily";q=1000;w=86400'), ("RateLimit", '"daily";r=50;t=3600'))
    assert (info.limit, info.window) == (1000, 86400)


def test_ietf_legacy():
    info = p(("RateLimit-Limit", "10, 10;w=1"), ("RateLimit-Remaining", "0"), ("RateLimit-Reset", "1"))
    assert (info.limit, info.window, info.remaining, info.reset) == (10, 1, 0, 1)
    assert info.styles == ("ietf-legacy",)
    assert info.hint() == (1, "RateLimit-Reset")


def test_both_styles_disagreeing_is_a_problem():
    info = p(("X-RateLimit-Remaining", "3"), ("RateLimit", '"default";r=0;t=5'))
    assert info.remaining == 0
    assert "disagree on remaining" in info.problems[0]


def test_raw_keeps_only_rate_headers_in_order():
    info = p(("Server", "x"), ("Retry-After", "2"), ("X-RateLimit-Limit", "5"), ("Date", DATE))
    assert info.raw == (("Retry-After", "2"), ("X-RateLimit-Limit", "5"))


def test_sf_parser():
    assert parse_sf_list('"a";q=1;w=2, b;x') == [("a", {"q": "1", "w": "2"}), ("b", {"x": True})]
    assert parse_sf_list("@@") is None
