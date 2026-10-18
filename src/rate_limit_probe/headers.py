"""Read rate-limit headers from a response, whichever style the API uses.

Supported:
  Retry-After                    seconds, or an HTTP date (RFC 9110)
  X-RateLimit-Limit/Remaining/Reset
                                 the common de facto style; also X-Rate-Limit-*.
                                 Reset may be seconds from now or a Unix time.
  RateLimit / RateLimit-Policy   the IETF draft's structured-field form:
                                 RateLimit: "default";r=4;t=7
                                 RateLimit-Policy: "default";q=5;w=10
  RateLimit-Limit/Remaining/Reset
                                 the IETF draft's earlier form

Times are converted to seconds after the response, measured against the
response's own Date header where there is one, so a skewed local clock does
not distort them.
"""

import dataclasses
import email.utils
import re

# Values this large can only be Unix times, not "seconds from now".
EPOCH_THRESHOLD = 1_000_000_000
EPOCH_MS_THRESHOLD = 100_000_000_000

RATE_HEADER = re.compile(r"^(retry-after|x-?rate-?limit-.*|ratelimit(-.*)?)$", re.IGNORECASE)


@dataclasses.dataclass(frozen=True)
class RateInfo:
    retry_after: float | None = None
    retry_after_form: str | None = None  # "seconds" or "date"
    limit: int | None = None
    remaining: int | None = None
    reset: float | None = None  # seconds until the quota resets
    window: float | None = None  # policy window, seconds (IETF only)
    styles: tuple = ()  # header styles seen, e.g. ("retry-after-seconds", "x-ratelimit")
    raw: tuple = ()  # (name, value) of every rate-limit header, as sent
    problems: tuple = ()  # malformed or contradictory values, in plain English

    def hint(self):
        """(seconds, source) the server says to wait, or (None, None).
        Retry-After first; then a quota reset when nothing is left."""
        if self.retry_after is not None:
            return self.retry_after, "Retry-After"
        if self.reset is not None and (self.remaining is None or self.remaining == 0):
            return self.reset, self.reset_source()
        return None, None

    def reset_source(self):
        if "ietf" in self.styles:
            return "RateLimit"
        if "ietf-legacy" in self.styles:
            return "RateLimit-Reset"
        return "X-RateLimit-Reset"


def rate_headers(headers):
    """Only the rate-limit headers, in order, as (name, value)."""
    return tuple((k, v) for k, v in headers if RATE_HEADER.match(k))


def _get(headers, *names):
    wanted = {n.lower() for n in names}
    for k, v in headers:
        if k.lower() in wanted:
            return k, v.strip()
    return None, None


def _int(value):
    try:
        return int(value.strip())
    except (AttributeError, ValueError):
        return None


def _date(value):
    try:
        d = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    return d.timestamp() if d.tzinfo is not None else None


# --- Structured fields (RFC 9651), just enough for the RateLimit headers -----

_ITEM_NAME = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|([A-Za-z*][A-Za-z0-9_\-.:%*/]*))')
_PARAM = re.compile(r'\s*;\s*([a-z*][a-z0-9_\-.*]*)(?:=(?:"((?:[^"\\]|\\.)*)"|([^;,\s]+)))?')


def parse_sf_list(value):
    """[(name, {param: value})] or None if the value is not a list of items."""
    items, pos = [], 0
    while pos < len(value):
        m = _ITEM_NAME.match(value, pos)
        if not m:
            return None
        name, pos, params = m.group(1) if m.group(1) is not None else m.group(2), m.end(), {}
        while (p := _PARAM.match(value, pos)):
            raw = p.group(2) if p.group(2) is not None else p.group(3)
            params[p.group(1)] = True if raw is None else raw
            pos = p.end()
        items.append((name, params))
        rest = value[pos:].lstrip()
        if not rest:
            break
        if not rest.startswith(","):
            return None
        pos = len(value) - len(rest) + 1
    return items


def _number(value):
    try:
        n = float(value)
    except (TypeError, ValueError):
        return None
    return n if n >= 0 else None


# --- Per style ---------------------------------------------------------------

def _retry_after(headers, ref, problems):
    _, value = _get(headers, "Retry-After")
    if value is None:
        return None, None
    if re.fullmatch(r"\d+", value):
        return float(value), "seconds"
    if re.fullmatch(r"\d+\.\d+", value):
        problems.append(f"Retry-After {value!r} has a fraction; the standard allows whole seconds only")
        return float(value), "seconds"
    when = _date(value)
    if when is None:
        problems.append(f"Retry-After {value!r} is neither whole seconds nor an HTTP date")
        return None, None
    if when < ref:
        problems.append("Retry-After is a date in the past")
    return max(0.0, when - ref), "date"


def _reset_value(name, value, ref, problems):
    n = _number(value)
    if n is None:
        problems.append(f"{name} {value!r} is not a number")
        return None
    if n >= EPOCH_MS_THRESHOLD:
        n /= 1000  # a Unix time in milliseconds
    if n >= EPOCH_THRESHOLD:
        if n < ref - 1:
            problems.append(f"{name} is a time in the past")
        return max(0.0, n - ref)
    return n


def _x_ratelimit(headers, ref, problems):
    prefixes = ("X-RateLimit-", "X-Rate-Limit-")
    found = {}
    for field in ("Limit", "Remaining", "Reset", "Reset-After"):
        name, value = _get(headers, *(p + field for p in prefixes))
        if value is not None:
            found[field] = (name, value)
    if not found:
        return None
    out = {}
    for field in ("Limit", "Remaining"):
        if field in found:
            name, value = found[field]
            out[field.lower()] = _int(value)
            if out[field.lower()] is None:
                problems.append(f"{name} {value!r} is not a whole number")
    if "Reset-After" in found:
        out["reset"] = _number(found["Reset-After"][1])
    elif "Reset" in found:
        out["reset"] = _reset_value(*found["Reset"], ref, problems)
    return out


def _ietf(headers, problems):
    _, value = _get(headers, "RateLimit")
    _, policy_value = _get(headers, "RateLimit-Policy")
    if value is None and policy_value is None:
        return None
    out = {}
    policies = {}
    if policy_value is not None:
        items = parse_sf_list(policy_value)
        if items is None:
            problems.append(f"RateLimit-Policy {policy_value!r} is not a valid structured field")
        else:
            policies = dict(items)
    if value is not None:
        items = parse_sf_list(value)
        if not items:
            problems.append(f"RateLimit {value!r} is not a valid structured field")
        else:
            name, params = items[0]
            out["remaining"] = _int(str(params.get("r", "")))
            out["reset"] = _number(params.get("t"))
            policies = {name: policies.get(name, next(iter(policies.values()), {}))} if policies else {}
    if policies:
        params = next(iter(policies.values()))
        out["limit"] = _int(str(params.get("q", "")))
        out["window"] = _number(params.get("w"))
    return out


def _ietf_legacy(headers, problems):
    _, limit = _get(headers, "RateLimit-Limit")
    _, remaining = _get(headers, "RateLimit-Remaining")
    _, reset = _get(headers, "RateLimit-Reset")
    if limit is None and remaining is None and reset is None:
        return None
    out = {}
    if limit is not None:
        # "10" or "10, 10;w=1"
        first, *policy = [p.strip() for p in limit.split(",")]
        out["limit"] = _int(first)
        for p in policy:
            m = re.search(r";\s*w=(\d+)", p)
            if m:
                out["window"] = float(m.group(1))
    if remaining is not None:
        out["remaining"] = _int(remaining)
    if reset is not None:
        out["reset"] = _number(reset)
    return out


def parse(headers, received_wall):
    """RateInfo for one response. headers is a list of (name, value);
    received_wall is the local Unix time the response arrived."""
    problems = []
    _, date_value = _get(headers, "Date")
    ref = _date(date_value) if date_value else None
    ref = received_wall if ref is None else ref

    styles = []
    retry_after, form = _retry_after(headers, ref, problems)
    if form:
        styles.append(f"retry-after-{form}")

    quota = {}
    xrl = _x_ratelimit(headers, ref, problems)
    ietf = _ietf(headers, problems)
    legacy = _ietf_legacy(headers, problems) if ietf is None else None
    for style, values in (("x-ratelimit", xrl), ("ietf", ietf), ("ietf-legacy", legacy)):
        if values is None:
            continue
        styles.append(style)
        for key, v in values.items():
            if v is None:
                continue
            if key in quota and key in ("limit", "remaining") and quota[key] != v:
                problems.append(f"the X-RateLimit and RateLimit headers disagree on {key} ({quota[key]} and {v})")
            quota[key] = v  # IETF wins over X-RateLimit when both are sent

    return RateInfo(
        retry_after=retry_after,
        retry_after_form=form,
        limit=quota.get("limit"),
        remaining=quota.get("remaining"),
        reset=quota.get("reset"),
        window=quota.get("window"),
        styles=tuple(styles),
        raw=rate_headers(headers),
        problems=tuple(problems),
    )
