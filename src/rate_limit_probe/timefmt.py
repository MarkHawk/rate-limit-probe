"""Seconds and counts, written for people."""


def secs(value):
    """2 s, 2.5 s, 12 s, 3 min 5 s."""
    if value is None:
        return "?"
    if value >= 120:
        m, s = divmod(round(value), 60)
        return f"{m} min {s} s" if s else f"{m} min"
    if value >= 10 or abs(value - round(value)) < 0.05:
        return f"{round(value)} s"
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
