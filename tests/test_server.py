import email.utils
import http.client

import pytest

from rate_limit_probe.clock import FakeClock
from rate_limit_probe.server.app import TestServer, scenario_endpoints
from rate_limit_probe.server.scenarios import BY_NAME, SCENARIOS, Endpoint, Scenario, scaled


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def server(clock):
    with TestServer(scenario_endpoints(), clock=clock) as s:
        yield s


def get(server, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    try:
        conn.request(method, path)
        r = conn.getresponse()
        return r.status, r.headers, r.read()
    finally:
        conn.close()


def statuses(server, path, n):
    return [get(server, path)[0] for _ in range(n)]


def test_binds_to_loopback_only(server):
    assert server.httpd.server_address[0] == "127.0.0.1"


def test_fixed_window_with_retry_after_seconds(server, clock):
    assert statuses(server, "/s/fixed-seconds", 6) == [200] * 5 + [429]
    clock.sleep(4)
    status, headers, _ = get(server, "/s/fixed-seconds")
    assert status == 429 and headers["Retry-After"] == "6"
    clock.sleep(6)
    assert get(server, "/s/fixed-seconds")[0] == 200


def test_retry_after_date_is_relative_to_the_date_header(server, clock):
    statuses(server, "/s/sliding-date", 5)
    clock.sleep(3)
    status, headers, _ = get(server, "/s/sliding-date")
    assert status == 429
    delta = (email.utils.parsedate_to_datetime(headers["Retry-After"])
             - email.utils.parsedate_to_datetime(headers["Date"])).total_seconds()
    assert delta == 7


def test_x_ratelimit_headers_count_down(server, clock):
    remaining = [get(server, "/s/xrl")[1]["X-RateLimit-Remaining"] for _ in range(5)]
    assert remaining == ["4", "3", "2", "1", "0"]
    status, headers, _ = get(server, "/s/xrl")
    assert status == 429 and "Retry-After" not in headers
    assert int(headers["X-RateLimit-Reset"]) == int(clock.wall()) + 10


def test_ietf_headers(server):
    _, headers, _ = get(server, "/s/bucket-ietf")
    assert headers["RateLimit-Policy"] == '"default";q=5;w=10'
    assert headers["RateLimit"].startswith('"default";r=4;t=')


def test_no_headers(server):
    statuses(server, "/s/none", 5)
    status, headers, _ = get(server, "/s/none")
    assert status == 429
    assert not [k for k in headers if "rate" in k.lower() or k.lower() == "retry-after"]


def test_lying_retry_after_is_too_short(server):
    statuses(server, "/s/lying", 5)
    status, headers, _ = get(server, "/s/lying")
    assert status == 429 and headers["Retry-After"] == "2"


def test_stale_remaining(server):
    assert {get(server, "/s/stale-remaining")[1]["X-RateLimit-Remaining"] for _ in range(5)} == {"4"}


def test_block_and_overload(server):
    assert statuses(server, "/s/block", 9) == [200] * 5 + [429, 429, 403, 403]
    statuses(server, "/s/overload-503", 5)
    status, headers, _ = get(server, "/s/overload-503")
    assert status == 503 and headers["Retry-After"] == "10"


def test_head_has_headers_and_no_body(server):
    status, headers, body = get(server, "/s/xrl", method="HEAD")
    assert status == 200 and body == b"" and headers["X-RateLimit-Remaining"] == "4"


def test_unknown_path_is_404_and_lists_paths(server):
    status, _, body = get(server, "/nope")
    assert status == 404 and b"/s/penalty" in body


def test_endpoints_are_independent(server):
    statuses(server, "/s/fixed-seconds", 6)
    assert get(server, "/s/xrl")[0] == 200


def test_scaled_scenario_and_validation():
    e = Endpoint(scaled(BY_NAME["fixed-seconds"], 2))
    for _ in range(5):
        e.respond(0, 0)
    assert e.respond(0.5, 0.5)[1] == [("Retry-After", "2")]
    with pytest.raises(ValueError):
        Scenario("x", "x", algorithm="leaky")
    with pytest.raises(ValueError):
        Scenario("x", "x", headers=("bogus",))


def test_every_scenario_has_a_unique_name():
    assert len({s.name for s in SCENARIOS}) == len(SCENARIOS)
