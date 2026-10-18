import http.client
import io
import threading

from rate_limit_probe import cli


def serve_and(argv, action):
    """Start `serve` in a thread, run action(server), then stop it."""
    out, err = io.StringIO(), io.StringIO()
    result = {}
    started = threading.Event()

    def ready(server):
        result["server"] = server
        started.set()

    t = threading.Thread(target=lambda: result.setdefault("code", cli.main(
        ["serve", "--port", "0", *argv], stdout=out, stderr=err, ready=ready)))
    t.start()
    assert started.wait(5)
    try:
        action(result["server"])
    finally:
        result["server"].httpd.shutdown()
        t.join(5)
    return result["code"], out.getvalue(), err.getvalue()


def get(server, path):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=5)
    conn.request("GET", path)
    r = conn.getresponse()
    r.read()
    conn.close()
    return r.status, dict(r.getheaders())


def test_serves_every_scenario_and_logs(monkeypatch):
    seen = []
    code, out, err = serve_and([], lambda s: seen.append(get(s, "/s/xrl")))
    assert code == 0 and seen[0][0] == 200
    assert "127.0.0.1 only" in out and "/s/penalty" in out and "/s/overload-503" in out
    assert "GET /s/xrl -> 200" in err


def test_custom_endpoint_and_window():
    seen = []

    def action(s):
        for _ in range(3):
            seen.append(get(s, "/custom"))

    code, out, err = serve_and(["--algorithm", "token-bucket", "--limit", "2", "--window", "4",
                                "--headers", "ietf,retry-after-seconds", "--quiet"], action)
    assert [x[0] for x in seen] == [200, 200, 429]
    assert seen[0][1]["RateLimit-Policy"] == '"default";q=2;w=4'
    assert "/custom" in out and err == ""


def test_bad_header_style_exits_2():
    out, err = io.StringIO(), io.StringIO()
    assert cli.main(["serve", "--algorithm", "fixed-window", "--headers", "bogus"], stdout=out, stderr=err) == 2
    assert "unknown header style" in err.getvalue()


def test_there_is_no_bind_option():
    from rate_limit_probe.serve import build_parser
    options = {o for a in build_parser()._actions for o in a.option_strings}
    assert not {"--bind", "--host", "--address", "--listen"} & options
