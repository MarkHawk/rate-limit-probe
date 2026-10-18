import pytest

from rate_limit_probe import safety
from rate_limit_probe.clock import FakeClock
from rate_limit_probe.safety import Budget, LimitExceeded, Limits, TargetRefused, make_limits, parse_target


# --- Limits and budget ---

def test_defaults():
    limits = make_limits()
    assert (limits.max_requests, limits.rate, limits.max_wait) == (100, 10, 60)
    assert limits.notes == ()


def test_values_above_a_ceiling_are_lowered_and_noted():
    limits = make_limits(max_requests=10_000, rate=1000, max_wait=86400)
    assert (limits.max_requests, limits.rate, limits.max_wait) == (500, 10, 300)
    assert len(limits.notes) == 3


@pytest.mark.parametrize("kwargs", [{"max_requests": 0}, {"rate": -1}, {"max_wait": float("nan")}])
def test_non_positive_values_are_refused(kwargs):
    with pytest.raises(ValueError):
        make_limits(**kwargs)


@pytest.mark.parametrize("kwargs", [{"max_requests": 501}, {"rate": 10.01}, {"max_wait": 301}])
def test_limits_cannot_be_built_above_a_ceiling(kwargs):
    with pytest.raises(ValueError):
        Limits(**kwargs)


def test_budget_refuses_after_the_cap():
    budget = Budget(make_limits(max_requests=3), FakeClock())
    for _ in range(3):
        budget.spend()
    with pytest.raises(LimitExceeded):
        budget.spend()
    assert budget.sent == 3 and budget.left == 0


def test_budget_paces_requests_to_the_rate():
    clock = FakeClock()
    budget = Budget(make_limits(rate=4), clock)
    times = []
    for _ in range(5):
        budget.spend()
        times.append(clock.now())
    assert times == pytest.approx([0, 0.25, 0.5, 0.75, 1.0])


def test_budget_clamps_even_if_limits_are_forged():
    """A Limits bypassing validation still cannot raise the ceilings."""
    forged = object.__new__(Limits)
    for k, v in {"max_requests": 10**6, "rate": 10**6, "max_wait": 10**6, "notes": ()}.items():
        object.__setattr__(forged, k, v)
    budget = Budget(forged, FakeClock())
    assert budget.cap == 500 and budget.interval == pytest.approx(0.1) and budget.max_wait == 300


def test_budget_needs_limits():
    with pytest.raises(TypeError):
        Budget({"max_requests": 10**6}, FakeClock())


def test_waits_are_capped():
    clock = FakeClock()
    budget = Budget(make_limits(max_wait=30), clock)
    budget.wait(30)
    with pytest.raises(LimitExceeded):
        budget.wait(31)
    assert clock.now() == 30


def test_there_is_no_flag_shaped_way_round_the_ceilings():
    assert safety.MAX_REQUESTS_CEILING == 500 and safety.RATE_CEILING == 10 and safety.MAX_WAIT_CEILING == 300


# --- Targets ---

@pytest.mark.parametrize("url, host, port", [
    ("http://localhost:8429/s/x", "localhost", 8429),
    ("http://LOCALHOST./", "localhost", 80),
    ("https://127.0.0.1/", "127.0.0.1", 443),
    ("http://127.5.6.7:1/", "127.5.6.7", 1),
    ("http://[::1]:8080/", "::1", 8080),
    ("http://[::ffff:127.0.0.1]/", "::ffff:127.0.0.1", 80),
])
def test_local_targets(url, host, port):
    t = parse_target(url)
    assert (t.host, t.port, t.is_local) == (host, port, True)


def test_path_and_query_are_kept():
    t = parse_target("http://localhost/a/b?x=1#frag")
    assert t.path == "/a/b?x=1" and t.url == "http://localhost/a/b?x=1"


@pytest.mark.parametrize("url", [
    "http://example.com/",
    "http://localhost.example.com/",
    "http://127.0.0.1.example.com/",
    "http://0.0.0.0/",
    "http://127.1/",  # resolves to loopback on some systems, but is not written as one
    "http://[::2]/",
])
def test_other_hosts_need_authorisation(url):
    with pytest.raises(TargetRefused, match="--i-am-authorised"):
        parse_target(url)
    assert parse_target(url.replace("127.1", "127.0.0.1"), authorised=True)


@pytest.mark.parametrize("url, reason", [
    ("http://localhost@example.com/", "user name or password"),
    ("http://user:pw@localhost/", "user name or password"),
    ("http://example.com\\@localhost/", "user name or password"),
    ("http://example.com#@localhost", "not a local address"),
    ("ftp://localhost/", "only http"),
    ("localhost:8429", "only http"),
    ("http:///path", "no host"),
    ("http://local host/", "spaces"),
    ("http://localhost:99999/", "port"),
    ("http://exa_mple.com/", "not a valid host name"),
])
def test_bypass_attempts_are_refused(url, reason):
    with pytest.raises(TargetRefused, match=reason):
        parse_target(url)


def test_authorised_warning_mentions_quota():
    t = parse_target("https://api.example.com/v1", authorised=True)
    text = safety.authorised_warning(t, make_limits())
    assert "api.example.com" in text and "quota" in text and "test key" in text


# --- Extra headers ---

def test_extra_headers():
    assert safety.check_extra_headers(["Authorization: Bearer abc", "X-Api-Key:k"]) == [
        ("Authorization", "Bearer abc"), ("X-Api-Key", "k")]


@pytest.mark.parametrize("raw", [
    "User-Agent: something-else", "X-Forwarded-For: 192.0.2.1", "host: other", "Forwarded: for=x",
    "no colon", "Bad Name: x", "X-A: a\r\nX-B: b",
])
def test_refused_headers(raw):
    with pytest.raises(ValueError):
        safety.check_extra_headers([raw])
