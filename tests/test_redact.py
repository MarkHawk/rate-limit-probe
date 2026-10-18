from rate_limit_probe.redact import display_url, mask_query
from rate_limit_probe.safety import parse_target


def test_mask_query():
    assert mask_query("/v1") == "/v1"
    assert mask_query("/v1?api_key=abc&page=2") == "/v1?api_key=<hidden>&page=<hidden>"
    assert mask_query("/v1?abc123secret") == "/v1?<hidden>"
    assert mask_query("/v1?=x&a=b") == "/v1?<hidden>&a=<hidden>"


def test_display_url():
    assert display_url(parse_target("http://localhost:8429/a?k=v")) == "http://localhost:8429/a?k=<hidden>"
