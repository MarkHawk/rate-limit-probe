import io
import socket

import pytest

from rate_limit_probe import cli
from rate_limit_probe.clock import FakeClock
from rate_limit_probe.server.app import TestServer, scenario_endpoints
from tls_helpers import make_ca_and_server, server_context


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def server(clock):
    with TestServer(scenario_endpoints(), clock=clock) as s:
        yield s


def run(argv, clock):
    out, err = io.StringIO(), io.StringIO()
    code = cli.main(argv, clock=clock, stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_penalty_report(server, clock):
    code, out, err = run([server.url("/s/penalty")], clock)
    assert code == 1
    for section in ("== Timeline ==", "== Findings ==", "== Likely cause ==", "Next step:", "Verdict: 1 problem"):
        assert section in out
    assert "sent early on purpose" in out
    assert "Probing" in err and "WARNING" not in err


def test_healthy_exit_zero(server, clock):
    code, out, _ = run([server.url("/s/fixed-seconds")], clock)
    assert code == 0 and "Verdict: no problems found" in out


def test_remote_host_is_refused_without_authorisation(clock, monkeypatch):
    monkeypatch.setattr(cli, "run_probe", lambda *a, **k: pytest.fail("must not probe"))
    code, out, err = run(["https://api.example.com/v1"], clock)
    assert code == 2 and out == "" and "--i-am-authorised" in err


def test_authorised_remote_host_warns_about_quota(clock, monkeypatch):
    """The probe itself is replaced, so nothing leaves the machine."""
    seen = {}

    def fake_probe(transport, budget, clock_, target, limits, method):
        seen["target"] = target
        raise SystemExit(0)

    monkeypatch.setattr(cli, "run_probe", fake_probe)
    err = io.StringIO()
    with pytest.raises(SystemExit):
        cli.main(["https://api.example.com/", "--i-am-authorised"], clock=clock, stdout=io.StringIO(), stderr=err)
    text = err.getvalue()
    assert "WARNING: api.example.com is not a local address" in text
    assert "quota" in text and "test key or a test environment" in text
    assert seen["target"].host == "api.example.com"


def test_ceilings_are_noted(server, clock):
    code, out, _ = run([server.url("/s/fixed-seconds"), "--max-requests", "9999", "--rate", "50"], clock)
    assert "Note:     --max-requests 9999 lowered to the ceiling of 500" in out
    assert "Note:     --rate 50/s lowered to the ceiling of 10/s" in out
    assert "up to 500 requests, 10/s or slower" in out


def test_header_values_and_query_values_never_appear(server, clock):
    code, out, err = run([server.url("/s/fixed-seconds?api_key=sekrit123"), "--header", "Authorization: Bearer tok999"],
                         clock)
    assert "sekrit123" not in out + err and "tok999" not in out + err
    assert "?api_key=<hidden>" in out


@pytest.mark.parametrize("argv, message", [
    (["http://localhost/", "--max-requests", "0"], "above 0"),
    (["http://localhost/", "--header", "User-Agent: x"], "not allowed"),
    (["http://localhost@example.com/"], "user name or password"),
    (["http://localhost/", "--cacert", "x.pem"], "https"),
])
def test_usage_errors_exit_2(clock, argv, message):
    code, out, err = run(argv, clock)
    assert code == 2 and message in err


def test_unreachable_exits_2(clock):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    code, out, _ = run([f"http://127.0.0.1:{port}/"], clock)
    assert code == 2
    assert "connection refused" in out and "could not reach the target" in out


def test_https_with_cacert(clock, tmp_path):
    tls = make_ca_and_server(tmp_path)
    with TestServer(scenario_endpoints(), clock=clock, ssl_context=server_context(tls)) as server:
        code, out, _ = run([server.url("/s/fixed-seconds", host="localhost"), "--cacert", str(tls["ca"])], clock)
        assert code == 0 and "https://localhost:" in out
        code, out, _ = run([server.url("/s/fixed-seconds", host="localhost")], clock)
        assert code == 2 and "--cacert" in out and "certificate verification failed" in out


def test_503_report_wording(server, clock):
    code, out, _ = run([server.url("/s/overload-503")], clock)
    assert code == 1
    assert "signalled overload or rate limiting with 503 instead of 429" in out


def test_json(server, clock):
    import json
    code, out, _ = run([server.url("/s/penalty?key=abc"), "--json"], clock)
    data = json.loads(out)
    assert code == 1 == data["summary"]["exit_code"]
    assert data["likely_cause"]["pattern"] == "early-retries-extend"
    assert {f["id"]: f["status"] for f in data["findings"]}["behaviour.early_retry"] == "FAIL"
    assert data["timeline"][5] == {**data["timeline"][5], "type": "request", "n": 6, "status": 429, "retry_after": 10}
    assert data["target"]["url"].endswith("/s/penalty?key=<hidden>") and "abc" not in out
    assert data["limits"] == {"max_requests": 100, "rate": 10, "max_wait": 60}


def test_json_never_contains_header_values(server, clock):
    code, out, _ = run([server.url("/s/fixed-seconds"), "--json", "--header", "X-Api-Key: zz-secret-zz"], clock)
    assert "zz-secret-zz" not in out


def test_redact_masks_host_and_path(clock, tmp_path):
    tls = make_ca_and_server(tmp_path)
    with TestServer(scenario_endpoints(), clock=clock, ssl_context=server_context(tls)) as server:
        url = server.url("/s/fixed-seconds", host="localhost")
        code, out, _ = run([url, "--redact"], clock)
        assert "localhost" not in out and "/s/fixed-seconds" not in out
        assert "https://<host>:" in out and "/<path>" in out and "Redacted:" in out
        # an error message that names the host is masked too
        code, out, _ = run([url, "--redact", "--json"], clock)
        assert "localhost" not in out and "<host>" in out
