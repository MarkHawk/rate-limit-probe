"""Render a Report as readable text."""

import json
import os
import textwrap

from rate_limit_probe import __version__
from rate_limit_probe.findings import Status
from rate_limit_probe.probe import Observation, Wait
from rate_limit_probe.redact import display_url
from rate_limit_probe.timefmt import plural, secs

WIDTH = 100
COLOURS = {Status.PASS: "32", Status.WARN: "33", Status.FAIL: "31;1", Status.INFO: "2"}
WAIT_REASON = {
    "early": "to test an early retry",
    "back-off": "backing off: no header said how long",
}


def use_colour(stream):
    return hasattr(stream, "isatty") and stream.isatty() and "NO_COLOR" not in os.environ


def _wrap(text, width, first, rest):
    lines = textwrap.wrap(text, width - len(rest), break_long_words=False, break_on_hyphens=False) or [""]
    return [first + lines[0]] + [rest + line for line in lines[1:]]


def _t(at):
    return f"{at:.1f} s"


def _note(o, stop_request):
    """What to show beside a single request."""
    info = o.info
    bits = []
    if o.error:
        bits.append(o.error)
    if o.status is not None and not o.ok:
        for name, value in info.raw:
            if name.lower() == "retry-after":
                bits.append(f"Retry-After: {value}" + (f" ({secs(info.retry_after)})" if info.retry_after_form == "date" else ""))
        if info.retry_after is None and info.hint()[0] is not None:
            bits.append(f"{info.reset_source()} in {secs(info.reset)}")
    if o.phase == "early":
        bits.append("sent early on purpose")
    if o.n == stop_request:
        bits.append("probe stopped")
    return "; ".join(bits)


def timeline_lines(result):
    """Requests grouped into runs of the same phase and status, with the waits between them."""
    out = []
    stop_request = result.stop.request if result.stop else None
    group = []

    def flush():
        if not group:
            return
        first, last = group[0], group[-1]
        num = f"#{first.n}" if first is last else f"#{first.n}-#{last.n}"
        when = _t(first.at) if first is last else f"{_t(first.at)[:-2]}-{_t(last.at)}"
        status = str(first.status) if first.status is not None else "---"
        if first is last:
            note = _note(first, stop_request)
        else:
            remaining = [o.info.remaining for o in group if o.info.remaining is not None]
            note = ("Remaining " + ", ".join(map(str, remaining))) if remaining else ""
        out.append(f"  {num:<9}{status:<5}{when:<13}{note}".rstrip())
        group.clear()

    for e in result.events:
        if isinstance(e, Observation):
            same = group and group[-1].phase == e.phase and group[-1].status == e.status and e.ok
            if not same:
                flush()
            group.append(e)
            if not e.ok:
                flush()
        else:
            flush()
            reason = WAIT_REASON.get(e.source, f"as {e.source} asked")
            out.append(f"  {'':<9}{'':<5}{'':<13}waited {secs(e.seconds)}, {reason}")
    flush()
    return out


def findings_lines(report, paint):
    width = max((len(f.title) for f in report.findings), default=0)
    indent = " " * (2 + 4 + 2 + width + 2)
    out = []
    for f in report.findings:
        head = f"  {paint(f.status.value.ljust(4), f.status)}  {f.title:<{width}}  "
        body = textwrap.wrap(f.summary, WIDTH - len(indent), break_long_words=False, break_on_hyphens=False)
        out.append(head + body[0])
        out += [indent + line for line in body[1:]]
    return out


def likely_cause_lines(report):
    cause = report.likely_cause
    if cause is None:
        return []
    out = ["", "== Likely cause =="]
    out += _wrap(cause.summary, WIDTH, "  ", "  ")
    out += _wrap(cause.next_step, WIDTH, "  Next step:  ", "              ")
    return out


def verdict_line(report):
    fails, warns = report.count(Status.FAIL), report.count(Status.WARN)
    counts = ", ".join(f"{report.count(s)} {s.value}" for s in (Status.FAIL, Status.WARN, Status.PASS, Status.INFO))
    if report.unreachable:
        verdict = "could not reach the target"
    elif fails:
        verdict = plural(fails, "problem") + " to fix"
        if warns:
            verdict += ", " + plural(warns, "warning") + " to review"
    elif warns:
        verdict = "no failures, " + plural(warns, "warning") + " to review"
    else:
        verdict = "no problems found"
    return f"Verdict: {verdict} ({counts})"


def limits_line(limits):
    return (f"up to {limits.max_requests} requests, {limits.rate:g}/s or slower, one connection, "
            f"waits up to {secs(limits.max_wait)}")


def render_text(report, colour=False):
    def paint(text, status):
        return f"\033[{COLOURS[status]}m{text}\033[0m" if colour else text

    r = report.result
    sent = len(r.observations)
    out = [
        f"rate-limit-probe {__version__}",
        f"Target:   {display_url(r.target)} ({'local' if r.target.is_local else 'authorised remote host'})",
        f"Limits:   {limits_line(r.limits)}",
        f"Sent:     {plural(sent, 'request')} ({r.method}) over {r.duration:.1f} s",
    ]
    out += [f"Note:     {n}" for n in report.notes]
    out += ["", "== Timeline =="] + timeline_lines(r)
    out += ["", "== Findings =="] + findings_lines(report, paint)
    out += likely_cause_lines(report)
    out += ["", verdict_line(report)]
    return "\n".join(out) + "\n"


def _json_value(v):
    if isinstance(v, Status):
        return v.value
    if isinstance(v, dict):
        return {str(k): _json_value(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_json_value(x) for x in v]
    if isinstance(v, float):
        return round(v, 3)
    if v is None or isinstance(v, (str, int, bool)):
        return v
    return str(v)


def _event(e):
    if isinstance(e, Wait):
        return {"type": "wait", "at": e.at, "seconds": e.seconds, "source": e.source, "purpose": e.purpose}
    return {
        "type": "request", "n": e.n, "phase": e.phase, "at": e.at, "status": e.status,
        "rate_headers": [[k, v] for k, v in e.info.raw],
        "retry_after": e.info.retry_after, "limit": e.info.limit, "remaining": e.info.remaining,
        "reset": e.info.reset, "error": e.error, "error_kind": e.error_kind,
    }


def report_dict(report):
    r = report.result
    cause = report.likely_cause
    return _json_value({
        "tool": "rate-limit-probe",
        "version": __version__,
        "target": {"url": display_url(r.target), "local": r.target.is_local, "method": r.method},
        "limits": {"max_requests": r.limits.max_requests, "rate": r.limits.rate, "max_wait": r.limits.max_wait},
        "notes": report.notes,
        "sent": len(r.observations),
        "duration": r.duration,
        "stop": None if r.stop is None else {
            "kind": r.stop.kind, "detail": r.stop.detail, "request": r.stop.request, "retry_after": r.stop.retry_after},
        "timeline": [_event(e) for e in r.events],
        "findings": [
            {"id": f.check_id, "status": f.status, "title": f.title, "summary": f.summary, "details": f.details}
            for f in report.findings
        ],
        "likely_cause": None if cause is None else {
            "pattern": cause.pattern, "label": cause.label, "summary": cause.summary, "next_step": cause.next_step},
        "summary": {
            **{s.value.lower(): report.count(s) for s in Status},
            "verdict": verdict_line(report).removeprefix("Verdict: "),
            "exit_code": report.exit_code,
        },
    })


def render_json(report):
    return json.dumps(report_dict(report), indent=2, ensure_ascii=False) + "\n"
