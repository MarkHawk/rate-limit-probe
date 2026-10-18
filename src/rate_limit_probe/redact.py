"""Keep secrets and personal data out of reports.

Query string values are always masked: API keys often travel there. Values
given with --header are never written anywhere, so they need no masking here.
With --redact the host and path are masked too, wherever they appear (a TLS
error, for example, names the host), so a report can go into a ticket
without naming the customer's API.
"""

HIDDEN = "<hidden>"


def mask_query(path):
    """/v1/items?api_key=abc&page=2 -> /v1/items?api_key=<hidden>&page=<hidden>.
    An item with no "=" may itself be a token, so it is hidden whole."""
    base, sep, query = path.partition("?")
    if not sep:
        return path
    items = []
    for item in query.split("&"):
        name, eq, _ = item.partition("=")
        items.append(f"{name}={HIDDEN}" if eq and name else HIDDEN)
    return base + "?" + "&".join(items)


def display_url(target):
    return f"{target.scheme}://{target.netloc}{mask_query(target.path)}"


REDACTED_HOST = "<host>"
REDACTED_PATH = "/<path>"


def redact_text(text, target):
    """Mask the target's path where the URL is shown, and its host everywhere."""
    text = text.replace(target.netloc + mask_query(target.path), target.netloc + REDACTED_PATH)
    return text.replace(target.host, REDACTED_HOST)
