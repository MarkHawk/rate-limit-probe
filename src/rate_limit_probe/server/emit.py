"""Turn a limiter's Decision into response headers in a chosen style."""

import email.utils
import math

STYLES = ("retry-after-seconds", "retry-after-date", "x-ratelimit", "ietf", "none")


def _seconds(value):
    return max(1, math.ceil(value - 1e-9))


def rate_headers(decision, styles, wall, *, retry_after_scale=1.0, stale_remaining=False):
    """A list of (name, value). Retry-After goes on refusals only; the quota
    headers go on every response, as most APIs do."""
    headers = []
    refused = not decision.allowed and decision.status is None
    if refused and decision.retry_after > 0:
        wait = _seconds(decision.retry_after * retry_after_scale)
        if "retry-after-seconds" in styles:
            headers.append(("Retry-After", str(wait)))
        elif "retry-after-date" in styles:
            headers.append(("Retry-After", email.utils.formatdate(math.floor(wall) + wait, usegmt=True)))
    remaining = decision.limit - 1 if stale_remaining else decision.remaining
    if "x-ratelimit" in styles:
        headers += [
            ("X-RateLimit-Limit", str(decision.limit)),
            ("X-RateLimit-Remaining", str(remaining)),
            ("X-RateLimit-Reset", str(math.ceil(wall + decision.reset_after))),
        ]
    if "ietf" in styles:
        headers += [
            ("RateLimit-Policy", f'"default";q={decision.limit};w={{window}}'),
            ("RateLimit", f'"default";r={remaining};t={math.ceil(decision.reset_after - 1e-9)}'),
        ]
    return headers
