import io
import json

import pytest

from rate_limit_probe import cli
from rate_limit_probe.clock import FakeClock

EXPECTED = {
    "fixed-seconds": "PASS", "sliding-date": "PASS", "bucket-ietf": "PASS", "xrl": "WARN", "none": "WARN",
    "lying": "FAIL", "stale-remaining": "FAIL", "penalty": "FAIL", "block": "FAIL", "overload-503": "FAIL",
}


def run(argv):
    out = io.StringIO()
    code = cli.main(["demo", *argv], clock=FakeClock(), stdout=out, stderr=io.StringIO())
    return code, out.getvalue()


def test_demo_lists_every_behaviour_with_the_expected_verdict():
    code, out = run([])
    assert code == 0
    assert out.startswith("rate-limit-probe demo: 10 behaviours on a local test server")
    lines = {line.split()[0]: line for line in out.splitlines() if line.startswith("  ")}
    for name, status in EXPECTED.items():
        assert f"  {status}  " in lines[name], lines[name]
    assert "likely cause: client retries during the limit" in lines["penalty"]
    assert "rate-limit-probe demo penalty" in out


@pytest.mark.parametrize("name", ["penalty", "lying"])
def test_demo_full_report(name):
    code, out = run([name])
    assert code == 1
    assert "== Likely cause ==" in out and "Note:     demo window shortened" in out


def test_demo_json():
    code, out = run(["overload-503", "--json"])
    data = json.loads(out)
    assert data["likely_cause"]["pattern"] == "503-as-limit"


def test_unknown_name():
    with pytest.raises(SystemExit) as e:
        run(["nope"])
    assert e.value.code == 2
