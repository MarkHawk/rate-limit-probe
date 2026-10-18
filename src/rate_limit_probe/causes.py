"""One likely cause and one next step, for the customer's ticket.

Patterns are tried most specific first and the first match wins. They read
the findings' details and the measured facts, never their wording. Every run
gets a cause, including a healthy one: if the server's limiting works as
advertised, the cause is on the client's side and the next step says where
to look.
"""

from dataclasses import dataclass

from rate_limit_probe.findings import Status
from rate_limit_probe.timefmt import plural, secs


@dataclass
class Cause:
    pattern: str  # stable id, e.g. "early-retries-extend"
    label: str  # a few words, for one-line summaries
    summary: str
    next_step: str


def _finding(report, check_id, status=None):
    f = report.finding(check_id)
    return f if f is not None and (status is None or f.status == status) else None


def _stopped(report, facts):
    stop = report.result.stop
    if stop is None:
        return None
    last = report.result.observations[-1] if report.result.observations else None
    if stop.kind == "blocked":
        return Cause(
            "blocked", "the server started blocking",
            f"After {plural(facts.retry_after_on_429[1], '429')} the server started answering {last.status}: it "
            "blocks clients that keep sending while limited. A client that retries through 429s can lock itself "
            "out this way.",
            "Pause the integration and ask the API owner whether the key or address is now blocked and how to "
            "lift it. Before resuming, make the client wait for Retry-After so it does not happen again.")
    if stop.kind == "overload-503":
        return Cause(
            "503-as-limit", "503 used as a rate limit",
            f"The server signalled overload or rate limiting with 503 instead of 429, asking for a "
            f"{secs(stop.retry_after)} wait. Many clients treat 503 as a server fault and retry at once, or give "
            "up, rather than waiting.",
            f"Make the client honour Retry-After on 503 as well as on 429 ({secs(stop.retry_after)} here), and "
            "treat a 503 that carries Retry-After as a rate limit.")
    if stop.kind == "wait-too-long":
        return Cause(
            "long-lockout", "a long lockout or quota",
            f"After {plural(facts.burst, 'request')} the server asked for a {secs(stop.retry_after)} wait: this "
            "looks like an hourly or daily quota, or a long lockout, not a short per-second limit.",
            "Check the API's documented quotas and the key's usage so far. To measure it, probe again with a "
            "longer --max-wait (up to 300 s).")
    if stop.kind == "budget":
        return Cause(
            "incomplete", "the probe ran out of requests",
            "The request budget ran out before the probe could see the limit reset.",
            "Run again with a higher --max-requests (up to 500), or a lower --rate so each phase needs fewer.")
    if stop.kind == "redirect":
        return Cause(
            "redirect", "the URL redirects",
            "The URL answered with a redirect. The probe never follows redirects, so it did not reach the API.",
            "Probe the URL the redirect points to, directly.")
    if stop.kind == "connection" and last is not None and last.error_kind == "tls":
        return Cause(
            "tls", "the certificate was not trusted",
            f"The HTTPS connection failed: {last.error}.",
            "If the API uses a private CA, pass its certificate with --cacert. Verification cannot be turned off.")
    if stop.kind == "connection":
        return Cause(
            "connection", "the connection failed",
            f"The probe could not complete a request: {stop.detail}.",
            "Check the URL, the port and that the API is reachable from here.")
    if stop.kind == "status" and last is not None and last.status == 401:
        return Cause(
            "unauthorised", "the request is not authorised",
            "The API answered 401, so the 429s cannot be probed until the request itself succeeds.",
            "Pass the test key's credentials with --header, e.g. --header 'Authorization: Bearer ...'.")
    return Cause(
        "stopped", "the probe stopped early",
        f"The probe stopped: {stop.detail}.",
        "Check that the URL works with a single request first, then probe again.")


def _penalty(report, facts):
    f = _finding(report, "behaviour.early_retry", Status.FAIL)
    if not f:
        return None
    return Cause(
        "early-retries-extend", "client retries during the limit",
        "Each request sent during the limit restarts it, so a client that retries immediately and ignores "
        "Retry-After never leaves the limit.",
        f"Make the client wait for the full Retry-After before retrying, and back off when there is none. Check "
        f"its logs for retries less than {secs(facts.hint)} apart after a 429.")


def _too_short(report, facts):
    f = _finding(report, "headers.retry_after", Status.FAIL) or _finding(report, "headers.quota", Status.FAIL)
    if not f or not f.details.get("too_short"):
        return None
    header = facts.hint_source
    actual = f"about {secs(facts.recovery_after)}" if facts.recovery_after is not None else "longer"
    refused = sum(1 for o in facts.honour if o.limited)
    return Cause(
        "retry-after-too-short", f"{header} is too short",
        f"The server's {header} ({secs(facts.hint)}) is much shorter than the real wait ({actual}), so a "
        f"well-behaved client retries too soon and is refused again ({plural(refused, 'time')} here).",
        f"This is on the API side, not the client's: send this report to the API owner. Until it is fixed, have "
        f"the client back off further when a retry after {header} is refused again ({actual} in all here).")


def _quota_wrong(report, facts):
    f = _finding(report, "headers.quota", Status.FAIL)
    if not f:
        return None
    return Cause(
        "quota-headers-wrong", "the quota headers are wrong",
        f"The {f.title} do not match what happened ({f.details['problems'][0][0].lower()}"
        f"{f.details['problems'][0][1:]}), so a client that paces itself from them still runs into 429s.",
        "Send this report to the API owner. Meanwhile, have the client rely on 429 and Retry-After rather "
        "than on the Remaining count.")


def _no_headers(report, facts):
    if facts.first_429 is None or facts.retry_after_on_429[0] or facts.hint is not None:
        return None
    lifted = ""
    if facts.backoff_bracket:
        lo, hi = facts.backoff_bracket
        lifted = f" Here the limit lifted between {secs(lo)} and {secs(hi)} after the first 429."
    at_least = f" and allowing at least {secs(facts.backoff_bracket[1])}" if facts.backoff_bracket else ""
    return Cause(
        "no-headers", "no rate-limit headers",
        "The API sends no Retry-After or rate-limit headers, so a client cannot know how long to wait, and a "
        "short fixed retry keeps hitting the limit." + lifted,
        f"Give the client exponential back-off with jitter on 429, starting at 1 s{at_least}. Ask the API owner "
        "to send Retry-After.")


def _no_retry_after(report, facts):
    if facts.first_429 is None or facts.retry_after_on_429[0] or facts.hint is None:
        return None
    return Cause(
        "retry-after-missing", "no Retry-After on 429",
        f"The API says when to retry only in {facts.hint_source}, not in Retry-After, so a client or HTTP "
        "library that only honours Retry-After retries blindly.",
        f"Make the client read {facts.hint_source} after a 429, or ask the API owner to add Retry-After.")


def _not_reached(report, facts):
    f = _finding(report, "limit.none")
    if not f:
        return None
    limits = report.result.limits
    return Cause(
        "limit-not-reached", "limit not reached",
        f"No 429 in {plural(f.details['accepted'], 'request')} at {limits.rate:g} per second, so this endpoint's "
        "limit is above what the probe reaches, or the customer's 429s depend on something else: the total "
        "traffic on their key, another endpoint, or another method.",
        "Compare with the integration's real request rate from its logs, and check whether several instances "
        "or workers share one key.")


def _healthy(report, facts):
    if report.count(Status.FAIL):
        return None
    window = report.finding("limit.observed")
    rate = ""
    if window and window.details.get("window"):
        rate = f" and bursts above {facts.burst} per {secs(window.details['window'])}"
    return Cause(
        "server-behaves", "the limit works as advertised",
        "The server's limit behaves as its headers say, and a client that honours them recovers, so the 429s "
        "most likely come from how the integration sends requests.",
        f"Check the integration's logs for retries sooner than Retry-After, several workers or instances "
        f"sharing one key{rate}.")


PATTERNS = (_stopped, _penalty, _too_short, _quota_wrong, _no_headers, _no_retry_after, _not_reached, _healthy)


def likely_cause(report):
    for pattern in PATTERNS:
        cause = pattern(report, report.facts)
        if cause:
            return cause
    return None
