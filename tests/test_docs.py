"""The README's quick start works as written, the docs list every flag,
behaviour, finding and cause, and the writing follows the house style."""

import io
import re

from conftest import ROOT
from rate_limit_probe import causes, cli, demo, serve
from rate_limit_probe.clock import FakeClock
from rate_limit_probe.server.scenarios import SCENARIOS

README = (ROOT / "README.md").read_text()
USAGE = (ROOT / "docs" / "usage.md").read_text()
FINDINGS = (ROOT / "docs" / "findings.md").read_text()
DOCS = [ROOT / "README.md", *(ROOT / "docs").glob("*.md")]


def section(text, heading):
    return text.split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def code_blocks(text):
    return re.findall(r"```\n(.*?)```", text, re.S)


def run_demo(*argv):
    out = io.StringIO()
    cli.main(["demo", *argv], clock=FakeClock(), stdout=out, stderr=io.StringIO())
    return out.getvalue()


def test_quick_start_comes_first():
    assert re.findall(r"^## .*", README, re.M)[0] == "## Try it in 1 minute"


def test_quick_start_install_is_three_commands():
    install = code_blocks(section(README, "Try it in 1 minute"))[0]
    assert len(install.strip().splitlines()) == 3


def test_quick_start_shows_the_demo_first_with_its_real_output():
    blocks = code_blocks(section(README, "Try it in 1 minute"))
    assert blocks[1] == "rate-limit-probe demo\n"
    assert blocks[2] == run_demo()


def test_full_report_example_is_real_output():
    blocks = code_blocks(section(README, "Try it in 1 minute"))
    assert blocks[3] == "rate-limit-probe demo penalty\n"
    port = re.compile(r"127\.0\.0\.1:\d+")
    assert port.sub("HOST", blocks[4]) == port.sub("HOST", run_demo("penalty"))


def test_readme_has_the_quota_warning_and_related_tools():
    assert "Probing uses up the API key's own quota" in README
    assert "test key or a test environment" in README
    related = section(README, "Related tools")
    assert "github.com/MarkHawk/token-inspector" in related and "github.com/MarkHawk/identity-lab" in related
    assert "Claude Code" in README


def test_usage_lists_every_option():
    options = []
    for parser in (cli.build_parser(), serve.build_parser(), demo.build_parser()):
        options += [o for a in parser._actions for o in a.option_strings if o.startswith("--")]
    missing = [o for o in options if o != "--help" and f"`{o}" not in USAGE]
    assert not missing


def test_usage_lists_every_behaviour():
    for s in SCENARIOS:
        assert f"| `{s.name}` |" in USAGE


def test_findings_doc_lists_every_finding_and_cause():
    import inspect

    from rate_limit_probe import analyse
    ids = set(re.findall(r'Finding\(\s*"([a-z_.]+)"', inspect.getsource(analyse)))
    assert ids and all(f"`{i}`" in FINDINGS for i in ids), ids
    patterns = set(re.findall(r'Cause\(\s*"([a-z0-9-]+)"', inspect.getsource(causes)))
    assert patterns and all(f"| `{p}` |" in FINDINGS for p in patterns), patterns


def test_no_em_dashes():
    for path in DOCS:
        assert "\u2014" not in path.read_text(), path.name


US_SPELLINGS = re.compile(r"\b(behavior|behaviors|authoriz\w*|analyz\w*|recogniz\w*|honor\w*|color|colors|center)\b")


def test_uk_english():
    for path in DOCS:
        assert not US_SPELLINGS.findall(path.read_text()), path.name


def test_vendor_neutral():
    names = re.compile(r"\b(AWS|Azure|Cloudflare|Google|GitLab|Stripe|Okta|Akamai|nginx)\b", re.I)
    for path in DOCS:
        assert not names.findall(path.read_text()), path.name
