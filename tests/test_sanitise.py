"""The checker finds each kind of private content, including inside base64,
without echoing the value. Test values are built at run time so no source
file contains a real-looking address, path or key header."""

import base64

from conftest import load_script

sanitise = load_script("sanitise")

PRIVATE_IP = ".".join(["192", "168", "1", "20"])
HOME = "/" + "home" + "/" + "someone/project"
EMAIL = "someone" + "@" + "mail.test-domain.co.uk"
KEY_HEADER = "-----BEGIN " + "RSA PRIVATE KEY" + "-----"


def kinds(text, personal=()):
    return set().union(*(sanitise.find_problems(v, personal) for v in sanitise.decoded_views(text)))


def test_each_kind_is_found():
    assert kinds(f"host {PRIVATE_IP}") == {"private IPv4 address"}
    assert kinds(f"path {HOME}") == {"home directory path"}
    assert kinds(f"mail {EMAIL}") == {"email address"}
    assert kinds(KEY_HEADER) == {"private key"}


def test_documentation_values_are_allowed():
    assert not kinds("192.0.2.10 127.0.0.1 /home/pi user@example.com noreply@anthropic.com")


def test_found_inside_base64():
    blob = base64.b64encode(f"some padding text so the blob is long enough {PRIVATE_IP}".encode()).decode()
    assert "private IPv4 address" in kinds(blob)


def test_personal_patterns(tmp_path):
    f = tmp_path / "patterns"
    f.write_text("# comment\nAcmeCorp\nre:(?i)secret-host\n")
    personal = sanitise.load_personal_patterns(f)
    assert kinds("works at AcmeCorp", personal)
    assert kinds("SECRET-HOST.lan", personal)
    assert not kinds("AcmeCorporation", personal)
    assert sanitise.load_personal_patterns(tmp_path / "absent") == []


def test_check_reports_kinds_not_values(tmp_path, capsys):
    (tmp_path / "f.txt").write_text(f"{PRIVATE_IP}\n")
    assert sanitise.main(["--check", str(tmp_path)]) == 1
    err = capsys.readouterr().err
    assert "private IPv4 address" in err
    assert PRIVATE_IP not in err


def test_compiled_caches_are_skipped(tmp_path):
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "x.pyc").write_text(PRIVATE_IP)
    assert sanitise.check_paths([tmp_path], personal=[]) == []
