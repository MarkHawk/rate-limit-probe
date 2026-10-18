"""The test suite itself never reaches beyond loopback."""

import socket

import pytest


def test_outbound_connection_is_refused():
    with pytest.raises(AssertionError, match="non-loopback"):
        with socket.socket() as s:
            s.connect(("192.0.2.1", 80))  # RFC 5737 documentation address


def test_name_lookup_is_refused():
    with pytest.raises(AssertionError, match="resolve"):
        socket.getaddrinfo("example.com", 443)


def test_loopback_is_allowed():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
