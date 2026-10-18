import socket

import pytest

from rate_limit_probe.clock import FakeClock
from rate_limit_probe.safety import Budget, LimitExceeded, make_limits, parse_target
from rate_limit_probe.server.app import TestServer, scenario_endpoints
from rate_limit_probe.transport import USER_AGENT, Transport
from tls_helpers import make_ca_and_server, server_context


@pytest.fixture
def clock():
    return FakeClock()


def transport(url, clock, **kwargs):
    limits = kwargs.pop("limits", make_limits())
    return Transport(parse_target(url), Budget(limits, clock), clock, **kwargs)


def test_sends_over_one_connection_and_paces(clock):
    with TestServer(scenario_endpoints(), clock=clock) as server, transport(server.url("/s/fixed-seconds"), clock) as t:
        responses = [t.send() for _ in range(6)]
        conn = t.conn
    assert [r.status for r in responses] == [200] * 5 + [429]
    assert [r.at for r in responses] == pytest.approx([0, 0.1, 0.2, 0.3, 0.4, 0.5])
    assert conn is not None  # kept alive throughout
    assert dict(responses[-1].headers)["Retry-After"] == "10"


def test_identifies_itself_and_sends_extra_headers(clock):
    seen = {}

    class Recorder:
        def respond(self, now, wall):
            return 200, [], b""

    with TestServer({"/": Recorder()}, clock=clock) as server:
        orig = server.httpd.RequestHandlerClass.do_GET

        def spy(handler):
            seen.update(handler.headers)
            orig(handler)

        server.httpd.RequestHandlerClass.do_GET = spy
        with transport(server.url("/"), clock, extra_headers=[("Authorization", "Bearer t")]) as t:
            t.send()
    assert seen["User-Agent"] == USER_AGENT and seen["Authorization"] == "Bearer t"


def test_head(clock):
    with TestServer(scenario_endpoints(), clock=clock) as server, transport(server.url("/s/xrl"), clock, method="HEAD") as t:
        assert t.send().status == 200


def test_only_get_and_head(clock):
    with pytest.raises(ValueError):
        transport("http://localhost/", clock, method="POST")


def test_budget_is_enforced(clock):
    with TestServer(scenario_endpoints(), clock=clock) as server:
        with transport(server.url("/s/xrl"), clock, limits=make_limits(max_requests=2)) as t:
            t.send(), t.send()
            with pytest.raises(LimitExceeded):
                t.send()


def test_connection_refused_is_reported(clock):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    with transport(f"http://127.0.0.1:{port}/", clock) as t:
        r = t.send()
    assert r.status is None and r.error_kind == "refused"


def test_reopens_after_idle(clock):
    with TestServer(scenario_endpoints(), clock=clock) as server, transport(server.url("/s/xrl"), clock) as t:
        t.send()
        first = t.conn
        clock.sleep(30)
        assert t.send().status == 200
        assert t.conn is not first


# --- HTTPS ---

@pytest.fixture
def tls(tmp_path):
    return make_ca_and_server(tmp_path)


def test_https_with_a_private_ca(clock, tls):
    with TestServer(scenario_endpoints(), clock=clock, ssl_context=server_context(tls)) as server:
        with transport(server.url("/s/xrl", host="localhost"), clock, cacert=str(tls["ca"])) as t:
            assert t.send().status == 200
        with transport(server.url("/s/xrl"), clock, cacert=str(tls["ca"])) as t:  # by IP address
            assert t.send().status == 200


def test_https_untrusted_certificate_is_refused(clock, tls):
    with TestServer(scenario_endpoints(), clock=clock, ssl_context=server_context(tls)) as server:
        with transport(server.url("/s/xrl", host="localhost"), clock) as t:
            r = t.send()
    assert r.error_kind == "tls" and "certificate verification failed" in r.error


def test_https_wrong_host_name_is_refused(clock, tmp_path):
    tls = make_ca_and_server(tmp_path, names=("other.test",), ips=())
    with TestServer(scenario_endpoints(), clock=clock, ssl_context=server_context(tls)) as server:
        with transport(server.url("/s/xrl", host="localhost"), clock, cacert=str(tls["ca"])) as t:
            r = t.send()
    assert r.error_kind == "tls" and "Hostname mismatch" in r.error


def test_cacert_must_be_readable(clock, tmp_path):
    with pytest.raises(ValueError, match="--cacert"):
        transport("https://localhost/", clock, cacert=str(tmp_path / "missing.pem"))
    (tmp_path / "junk.pem").write_text("not a certificate")
    with pytest.raises(ValueError, match="--cacert"):
        transport("https://localhost/", clock, cacert=str(tmp_path / "junk.pem"))


def test_cacert_needs_https(clock, tls):
    with pytest.raises(ValueError, match="https"):
        transport("http://localhost/", clock, cacert=str(tls["ca"]))


def test_verification_cannot_be_turned_off():
    import inspect

    from rate_limit_probe import transport as mod
    source = inspect.getsource(mod)
    assert "CERT_NONE" not in source and "check_hostname = False" not in source
    assert "_create_unverified_context" not in source
