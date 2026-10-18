"""Turn a probe run into findings.

Facts are measured first (when the 429s started, how long the server asked
the client to wait, whether that was enough) and findings are written from
the facts, so causes.py and --json can use the same numbers.

A probe of a few dozen requests cannot always tell algorithms apart: a fixed
and a sliding window look the same from one burst. Findings say "consistent
with" and stop there.
"""

import dataclasses
import math

from rate_limit_probe.causes import likely_cause
from rate_limit_probe.findings import Finding, Report, Status
from rate_limit_probe.timefmt import join, ordinal, plural, secs

# Retry-After is whole seconds, so allow for rounding before calling a value wrong.
TOLERANCE = 1.5

STYLE_NAMES = {
    "x-ratelimit": "X-RateLimit",
    "ietf": "RateLimit",
    "ietf-legacy": "RateLimit-Limit/Remaining/Reset",
}


@dataclasses.dataclass
class Facts:
    first_429: object = None  # Observation
    burst: int = 0  # requests accepted before the first 429
    hint: float | None = None  # what the first 429 said to wait
    hint_source: str | None = None
    early: object = None
    early_delay: float | None = None  # seconds after the first 429
    early_expected: float | None = None  # wait left at that point, by the first hint
    early_hint: float | None = None  # what the early refusal said to wait
    penalised: bool | None = None  # None: cannot tell
    honour: list = dataclasses.field(default_factory=list)
    honour_waits: list = dataclasses.field(default_factory=list)
    recovered: object = None
    recovery_after: float | None = None  # seconds from the first 429 to the first acceptance after it
    backoff_bracket: tuple | None = None  # (refused at, accepted at), seconds after the first 429
    refill_accepted: int | None = None
    refill_hit_limit: bool = False
    styles: tuple = ()  # every header style seen
    retry_after_on_429: tuple = (0, 0)  # (429s with Retry-After, all 429s)
    remaining_seq: tuple = ()  # Remaining on the accepted discovery requests
    policy_limit: int | None = None
    policy_window: float | None = None


def measure(result):
    f = Facts()
    obs = result.observations
    styles = []
    for o in obs:
        styles += [s for s in o.info.styles if s not in styles]
    f.styles = tuple(styles)
    limited = [o for o in obs if o.limited]
    f.retry_after_on_429 = (sum(1 for o in limited if o.info.retry_after is not None), len(limited))
    for o in obs:
        if o.info.limit is not None and f.policy_limit is None:
            f.policy_limit = o.info.limit
        if o.info.window is not None and f.policy_window is None:
            f.policy_window = o.info.window

    discover = result.phase("discover")
    f.first_429 = next((o for o in discover if o.limited), None)
    f.burst = sum(1 for o in discover if o.ok)
    f.remaining_seq = tuple(o.info.remaining for o in discover if o.ok and o.info.remaining is not None)
    if f.first_429 is None:
        return f
    f.hint, f.hint_source = f.first_429.info.hint()

    early = result.phase("early")
    if early:
        f.early = early[0]
        f.early_delay = f.early.at - f.first_429.at
        if f.hint is not None:
            f.early_expected = max(0.0, f.hint - f.early_delay)
        if f.early.limited:
            f.early_hint, _ = f.early.info.hint()
            if f.early_hint is not None and f.early_expected is not None:
                f.penalised = f.early_hint > f.early_expected + TOLERANCE
        if f.early.ok:
            f.recovered = f.early

    f.honour = result.phase("honour")
    f.honour_waits = [w for w in result.waits if w.purpose == "honour"]
    if f.recovered is None:
        f.recovered = next((o for o in f.honour if o.ok), None)
    if f.recovered is not None:
        f.recovery_after = f.recovered.at - f.first_429.at
        if f.honour_waits and all(w.source == "back-off" for w in f.honour_waits):
            refused = [o for o in [f.first_429, *early, *f.honour] if o.limited]
            f.backoff_bracket = (refused[-1].at - f.first_429.at, f.recovery_after)
        refill = result.phase("refill")
        f.refill_accepted = 1 + sum(1 for o in refill if o.ok)
        f.refill_hit_limit = bool(refill) and refill[-1].limited
    return f


# --- Findings ----------------------------------------------------------------

def _start(result, f):
    obs = result.observations
    stop = result.stop
    if f.first_429 is None and stop is not None and stop.request is not None and f.burst:
        o = obs[stop.request - 1]
        return Finding("limit.start", Status.INFO, "When limiting starts",
                       f"Request {o.n}, {secs(o.at)} in, was answered {o.status or 'with an error'} (not 429): "
                       f"{plural(f.burst, 'request was', 'requests were')} accepted first.",
                       {"request": o.n, "at": o.at, "burst": f.burst})
    if f.first_429 is None:
        if stop is not None:
            return None
        accepted = sum(1 for o in obs if o.ok)
        if not accepted:
            return None
        span = obs[-1].at if obs else 0
        return Finding("limit.none", Status.INFO, "No limit reached",
                       f"No 429 in {plural(accepted, 'request')} over {secs(span)} at up to "
                       f"{result.limits.rate:g} per second. The limit is higher than this probe goes, or it "
                       "counts something other than this client's requests.",
                       {"accepted": accepted})
    o = f.first_429
    return Finding("limit.start", Status.INFO, "When 429s start",
                   f"Request {o.n}, {secs(o.at)} in: {plural(f.burst, 'request was', 'requests were')} "
                   "accepted first.",
                   {"request": o.n, "at": o.at, "burst": f.burst})


def _window_estimate(f):
    """Seconds from the first request until the quota came back, as best known."""
    if f.policy_window:
        return f.policy_window, "RateLimit-Policy"
    # The first 429's own hint, if a single wait proved it right. An early
    # retry that restarted the limit does not make the window longer.
    clean = f.hint is not None and len(f.honour) <= 1 and f.recovered is not None
    if clean:
        return f.first_429.at + f.hint, f.hint_source
    if f.recovered is not None:
        return f.recovered.at, "observed"
    return None, None


def _observed(f):
    if f.first_429 is None:
        return None
    details = {"burst": f.burst, "policy_limit": f.policy_limit, "policy_window": f.policy_window}
    if f.recovered is None:
        return Finding("limit.observed", Status.INFO, "Observed limit",
                       f"{plural(f.burst, 'request')}, then refused; the probe did not get back in, so the "
                       "window is unknown.", details)
    window, source = _window_estimate(f)
    # A measured reset is an upper bound: it is seen up to one request late, and
    # Retry-After rounds up. So the window is quoted rounded down.
    nice = max(1, math.floor(window + 0.05)) if window and source != "RateLimit-Policy" else window
    details.update(window=window, window_nice=nice, window_source=source, refill_accepted=f.refill_accepted)
    gradual = f.refill_hit_limit and f.refill_accepted is not None and f.refill_accepted < f.burst
    details["shape"] = "gradual" if gradual else "full" if f.refill_hit_limit else None
    if gradual:
        text = (f"A burst of {plural(f.burst, 'request')}, then after a {secs(f.recovery_after)} wait only "
                f"{plural(f.refill_accepted, 'request was', 'requests were')} accepted before the next 429. Capacity "
                "comes back gradually: consistent with a token bucket")
        if f.policy_limit and f.policy_window:
            text += f" holding {f.policy_limit} and refilling over {secs(f.policy_window)}"
        return Finding("limit.observed", Status.INFO, "Observed limit", text + ".", details)
    if f.backoff_bracket:
        lo, hi = f.backoff_bracket
        text = (f"{plural(f.burst, 'request')}, then refused until somewhere between {secs(f.first_429.at + lo)} "
                f"and {secs(f.first_429.at + hi)} after the first request (found by backing off, as no "
                "header said).")
    else:
        text = (f"{plural(f.burst, 'request')}, then refused until {window:.1f} s after the first "
                "request. Consistent with a fixed or sliding window")
        text += f" of {f.burst} per {secs(nice)}" if window else ""
        confirm = []
        if f.policy_limit == f.burst:
            confirm.append(f"the Limit header ({f.policy_limit})")
        if f.policy_window:
            confirm.append(f"RateLimit-Policy (w={f.policy_window:g})")
        text += ", which " + join(confirm) + " confirms." if confirm else "."
    return Finding("limit.observed", Status.INFO, "Observed limit", text, details)


def _honour_failed_text(f, header):
    waits = [w for w in f.honour_waits]
    total = sum(w.seconds for w in waits)
    refused = sum(1 for o in f.honour if o.limited)
    text = (f"{header} is shorter than the real wait. The first 429 said {secs(f.hint)}; the probe waited as "
            f"asked {plural(len(waits), 'time')} ({secs(total)} in all) and was refused "
            f"{plural(refused, 'time')}")
    if f.recovered is not None:
        text += f". Requests were accepted again {secs(f.recovery_after)} after the first 429."
    else:
        text += ", and never got back in."
    return text


def _honour_failed(f):
    """True when waiting exactly as the server said was not enough."""
    told = [w for w in f.honour_waits if w.source != "back-off"]
    return bool(told) and any(o.limited for o in f.honour)


def _retry_after(f):
    if f.first_429 is None:
        return None
    with_ra, total = f.retry_after_on_429
    details = {"with": with_ra, "of": total}
    if with_ra == 0:
        if f.hint is not None:
            text = (f"No 429 carried Retry-After. The wait came from {f.hint_source} instead, so a client that "
                    "only reads Retry-After has to guess.")
        else:
            text = "No 429 carried Retry-After, and no other header said when to retry. Clients have to guess."
        return Finding("headers.retry_after", Status.WARN, "Retry-After", text, details)
    forms = sorted({o.info.retry_after_form for o in [f.first_429, f.early, *f.honour]
                    if o is not None and o.info.retry_after is not None})
    form = ("as an HTTP date" if forms == ["date"] else "in seconds" if forms == ["seconds"]
            else "in seconds or as a date")
    sent = "Sent on every 429" if with_ra == total else f"Sent on {with_ra} of {total} 429s"
    details["form"] = forms
    if f.hint_source == "Retry-After" and _honour_failed(f):
        return Finding("headers.retry_after", Status.FAIL, "Retry-After", _honour_failed_text(f, "Retry-After"),
                       {**details, "too_short": True, "said": f.hint, "actual": f.recovery_after})
    if f.early is not None and f.early.ok and f.hint_source == "Retry-After":
        return Finding("headers.retry_after", Status.WARN, "Retry-After",
                       f"{sent}, {form}, but it is longer than needed: it said {secs(f.hint)} and a request "
                       f"sent after {secs(f.early_delay)} was accepted.", {**details, "too_long": True})
    status = Status.PASS if with_ra == total else Status.WARN
    tail = "Waiting as asked was enough: the next request was accepted." if f.recovered is not None else ""
    return Finding("headers.retry_after", status, "Retry-After", f"{sent}, {form}. {tail}".strip(), details)


def _quota(result, f):
    quota_styles = [s for s in f.styles if s in STYLE_NAMES]
    if not quota_styles:
        return Finding("headers.quota", Status.INFO, "Quota headers",
                       "No X-RateLimit or RateLimit headers, so a client cannot see the limit coming.",
                       {"styles": []})
    name = " and ".join(STYLE_NAMES[s] for s in quota_styles)
    details = {"styles": quota_styles, "remaining": list(f.remaining_seq)}
    problems, good = [], []
    seq = list(f.remaining_seq)
    if seq:
        counts_down = all(b == a - 1 for a, b in zip(seq, seq[1:]))
        if not counts_down:
            problems.append("Remaining did not count down (it said " + ", ".join(map(str, seq)) + ")")
        if f.first_429 is not None and seq[-1] > 0:
            who = "it" if problems else "Remaining"
            problems.append(f"{who} still said {seq[-1]} left when the next request was refused")
        if counts_down and (f.first_429 is None or seq[-1] == 0):
            good.append("Remaining counted down" + (" to 0" if f.first_429 is not None else ""))
    if f.policy_limit is not None and f.first_429 is not None:
        fresh = seq and seq[0] == f.policy_limit - 1
        if fresh and f.policy_limit != f.burst:
            problems.append(f"Limit said {f.policy_limit} but {f.burst} were accepted")
        elif f.policy_limit == f.burst:
            good.append("Limit matched what happened")
    if f.first_429 is not None:
        reset = f.first_429.info.reset
        ra = f.first_429.info.retry_after
        if reset is not None and ra is not None:
            if reset + TOLERANCE < ra:
                problems.append(f"Reset ({secs(reset)}) is sooner than Retry-After ({secs(ra)})")
            elif abs(reset - ra) <= TOLERANCE:
                good.append("Reset agreed with Retry-After")
    details["problems"] = problems
    if f.hint_source not in (None, "Retry-After") and _honour_failed(f):
        return Finding("headers.quota", Status.FAIL, f"{name} headers",
                       _honour_failed_text(f, f.hint_source), {**details, "too_short": True})
    if problems:
        return Finding("headers.quota", Status.FAIL, f"{name} headers",
                       join(problems) + ". A client that paces itself from these headers will still hit 429.",
                       details)
    text = (join(good) + "." if good else "Sent, and nothing in them contradicted what happened.")
    return Finding("headers.quota", Status.PASS, f"{name} headers", text[0].upper() + text[1:], details)


def _malformed(result):
    problems = []
    for o in result.observations:
        problems += [p for p in o.info.problems if p not in problems]
    if not problems:
        return None
    return Finding("headers.malformed", Status.WARN, "Header format",
                   "; ".join(problems[:3]) + ("; and more" if len(problems) > 3 else "") + ".",
                   {"problems": problems})


def _early(f):
    if f.early is None:
        return None
    d = {"delay": f.early_delay, "expected": f.early_expected, "new_hint": f.early_hint, "penalised": f.penalised}
    if f.early.ok:
        return Finding("behaviour.early_retry", Status.INFO, "Early retry",
                       f"A request sent {secs(f.early_delay)} after the 429 was accepted.", d)
    if f.penalised:
        return Finding("behaviour.early_retry", Status.FAIL, "Early retry",
                       f"A request sent {secs(f.early_expected)} early was refused and the wait went back up to "
                       f"{secs(f.early_hint)}: the server restarts the limit on every early request. A client "
                       "that retries before the wait is over stays limited for as long as it keeps retrying.", d)
    if f.penalised is None:
        return Finding("behaviour.early_retry", Status.INFO, "Early retry",
                       f"A request {secs(f.early_delay)} after the 429 was refused. With no wait in the headers, "
                       "the probe cannot tell whether early retries extend the limit.", d)
    return Finding("behaviour.early_retry", Status.PASS, "Early retry",
                   f"A request sent {secs(f.early_expected)} early was refused, and did not extend the wait.", d)


def _recovery(f):
    if f.first_429 is None or (f.early is not None and f.early.ok):
        return None
    if f.recovered is None:
        if not any(o.limited for o in f.honour):
            return None  # stopped before a retry was refused; the stop finding says why
        return Finding("behaviour.recovery", Status.FAIL, "Recovery",
                       f"After {plural(len(f.honour), 'patient retry', 'patient retries')} the probe was still "
                       "refused.", {"recovered": False})
    d = {"recovered": True, "after": f.recovery_after}
    if f.backoff_bracket:
        steps = ", ".join(f"{w.seconds:g}" for w in f.honour_waits)
        return Finding("behaviour.recovery", Status.PASS, "Recovery",
                       f"Backing off ({steps} s) worked: accepted again {secs(f.recovery_after)} after the first "
                       "429.", d)
    attempts = len(f.honour)
    if attempts > 1:
        return Finding("behaviour.recovery", Status.WARN, "Recovery",
                       f"A client that keeps waiting as told does get back in, but only at the {ordinal(attempts)} "
                       f"try, {secs(f.recovery_after)} after the first 429.", {**d, "attempts": attempts})
    return Finding("behaviour.recovery", Status.PASS, "Recovery",
                   f"A client that waits as told gets back in: accepted {secs(f.recovery_after)} after the first "
                   "429, at the first try.", {**d, "attempts": attempts})


def _reset(f):
    if f.refill_accepted is None or not f.refill_hit_limit:
        return None
    if f.refill_accepted >= f.burst:
        text = f"After the wait, the full {plural(f.burst, 'request')} were available again at once."
    else:
        text = (f"After the wait, {plural(f.refill_accepted, 'request was', 'requests were')} accepted before "
                f"the next 429, not the full {f.burst}.")
    return Finding("behaviour.reset", Status.INFO, "Reset", text, {"accepted": f.refill_accepted, "burst": f.burst})


def _stopped(result, f):
    s = result.stop
    if s is None:
        return None
    limited_before = sum(1 for o in result.observations if o.limited)
    d = {"kind": s.kind, "request": s.request, "retry_after": s.retry_after}
    if s.kind == "blocked":
        status = result.observations[-1].status
        text = (f"Request {s.request} was answered {status} after {plural(limited_before, '429')}: the server "
                "moved from rate limiting to blocking. The probe stopped there.")
    elif s.kind == "overload-503":
        text = (f"Request {s.request} was answered 503 with Retry-After {secs(s.retry_after)}: the server "
                "signalled overload or rate limiting with 503 instead of 429. The probe stopped there, as it "
                "does on any 5xx.")
    elif s.kind == "wait-too-long":
        return Finding("run.stopped", Status.WARN, "Stopped early",
                       f"The server asked for a {secs(s.retry_after)} wait, longer than the "
                       f"{secs(result.limits.max_wait)} allowed (--max-wait). The probe stopped rather than wait.",
                       d)
    elif s.kind == "budget":
        return Finding("run.stopped", Status.WARN, "Stopped early",
                       f"The request budget ({result.limits.max_requests}) ran out before the probe finished.", d)
    elif s.kind == "connection":
        text = f"Request {s.request}: {s.detail}. The probe stopped there."
    else:
        text = f"Request {s.request}: {s.detail}. The probe stopped there."
    return Finding("run.stopped", Status.FAIL, "Stopped early", text, d)


def analyse(result, notes=()):
    facts = measure(result)
    findings = [
        _start(result, facts),
        _observed(facts),
        _retry_after(facts),
        _quota(result, facts) if result.observations and any(o.status for o in result.observations) else None,
        _malformed(result),
        _early(facts),
        _recovery(facts),
        _reset(facts),
        _stopped(result, facts),
    ]
    report = Report(result, facts, [x for x in findings if x is not None], notes=list(notes))
    report.likely_cause = likely_cause(report)
    return report
