# Design

## What it is for

A support engineer handed "our integration keeps getting 429 Too Many
Requests" usually has only the customer's description and a few log lines.
Whether the fault is the client (retrying too fast, ignoring Retry-After,
several workers sharing a key) or the API (a misleading Retry-After, headers
that do not count down, a 503 where a 429 belongs) is hard to tell from
there. rate-limit-probe measures the API's limiting directly, with as few
requests as it can, and says which side is at fault and what to do next.

## Principles

**A diagnostic, not a load tool.** The limits in `safety.py` are constants:
at most 500 requests a run, at most 10 a second, one connection, waits of at
most 300 s. There is no flag to remove them. `Limits` refuses to hold a value
above a ceiling and `Budget` clamps again, and every request goes through
`Budget.spend()`, so no code path can send more or faster. The tests try to
get round each limit.

**Local by default.** Only localhost and loopback addresses are probed unless
`--i-am-authorised` is given, and the check reads the host as written in the
URL, not what it resolves to. URLs with credentials, odd host forms and
redirects are refused or stop the run, because each could point the probe
somewhere the check did not see. Probing a real API uses up the key's own
quota, so the warning says to use a test key or a test environment.

**No evasion.** One connection, a fixed and honest User-Agent, no IP or
header rotation, and headers that would disguise the client are refused. The
probe stops at the first sign of a block rather than working round it.

**Nothing hidden.** `http.client` is used directly: no retries, no redirects,
no proxies from the environment, no connection pool. HTTPS always verifies;
`--cacert` only chooses which CA to trust.

**Honest about uncertainty.** A few dozen requests cannot always tell a fixed
window from a sliding one, so the report says "consistent with". A measured
reset is an upper bound (seen up to one request late, and Retry-After rounds
up), so the window is quoted rounded down. With no headers, the reset is
given as a range.

**Standard library only.** The tool has no runtime dependencies, so there is
nothing extra to vet on a support laptop. The tests use pytest, and
cryptography for a throwaway CA.

**No personal data.** Reports mask query string values always and never show
`--header` values; `--redact` masks the host and path too. The repository is
checked by the same hygiene tests as token-inspector: no private addresses,
home directories, email addresses, keys or certificates, and no strings from
a local, gitignored `.personal-patterns` file.

## How it works

- `probe.py` runs the four phases (discover, early retry, honour, refill)
  and records every request and wait.
- `headers.py` reads Retry-After, X-RateLimit-* and the IETF RateLimit headers
  into one shape, measuring times against the response's own Date header.
- `analyse.py` measures facts from the run and writes findings from them;
  `causes.py` picks one likely cause from the findings' details, never their
  wording.
- `server/` is the test server: three algorithms (fixed window, sliding log,
  token bucket), wrappers that misbehave on purpose (penalty, block), and the
  header styles. It shares an injectable clock with the probe, so the tests
  run the real HTTP exchange on a fake clock in seconds, while the demo uses
  the real one.

## Ideas not built

- Probing two endpoints to see whether they share a limit.
- Reading a client's own log of requests and 429s to point at the retry that
  went wrong, offline.
- A `--policy` flag to compare the observed limit with the documented one.
