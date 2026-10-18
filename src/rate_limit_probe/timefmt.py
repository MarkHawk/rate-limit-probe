"""Seconds and counts, written for people."""

import math


def _whole(value):
    """Round half down, with a little slack for float noise, so 10.5 s measured
    (a 10 s window seen 0.5 s late) always reads as 10 s, never sometimes 11."""
    return math.floor(value + 0.5 - 1e-6)


def secs(value):
    """2 s, 2.5 s, 12 s, 3 min 5 s."""
    if value is None:
        return "?"
    if value >= 120:
        m, s = divmod(_whole(value), 60)
        return f"{m} min {s} s" if s else f"{m} min"
    if value >= 10 or abs(value - round(value)) < 0.05:
        return f"{_whole(value)} s"
    return f"{value:.1f} s"


def plural(n, word, many=None):
    return f"{n} {word if n == 1 else (many or word + 's')}"


def join(words):
    words = list(words)
    if len(words) <= 1:
        return "".join(words)
    return ", ".join(words[:-1]) + " and " + words[-1]


def ordinal(n):
    suffix = "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"
