"""Keep secrets and personal data out of reports.

Query string values are always masked: API keys often travel there. Values
given with --header are never written anywhere, so they need no masking here.
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
