# Findings and likely causes

Every finding has a status:

| Status | Meaning |
|---|---|
| PASS | The server did what its headers promised. |
| WARN | Worth a look, but a careful client copes. |
| FAIL | Something here will cause 429s, or keep a client stuck in them. |
| INFO | A measurement, neither good nor bad. |

Retry-After is whole seconds, so a wait is only called wrong when it is out
by more than 1.5 s. A short probe cannot always tell algorithms apart (a
fixed and a sliding window look the same from one burst), so the report says
"consistent with" rather than naming one.

## Findings

| Id | Title | What it reports |
|---|---|---|
| `limit.start` | When 429s start | The request number and time of the first 429 (or of the 503 or 403 that stopped the run), and how many requests were accepted first. |
| `limit.none` | No limit reached | No 429 within the budget: the limit is higher than the probe goes, or counts something else. |
| `limit.observed` | Observed limit | The burst, how long until requests were accepted again, and whether the full limit came back at once (fixed or sliding window) or gradually (token bucket). The measured time is an upper bound, so the window is quoted rounded down. Headers that state the limit or window are named when they agree. |
| `headers.retry_after` | Retry-After | PASS when it is sent on every 429 and waiting that long works. FAIL when waiting as told was refused again: Retry-After is shorter than the real wait. WARN when it is missing, on only some 429s, or clearly longer than needed. |
| `headers.quota` | X-RateLimit / RateLimit headers | PASS when Remaining counts down to 0 before the 429, Limit matches the burst and Reset agrees with Retry-After. FAIL when Remaining never moves, says requests are left when the next is refused, Limit promises more than was accepted, or waiting for Reset was not enough. INFO when there are none. |
| `headers.malformed` | Header format | WARN for values that break the standards: a fractional Retry-After, a date in the past, a non-numeric Reset. |
| `behaviour.early_retry` | Early retry | PASS when a request sent before the wait is over is refused without extending it. FAIL when the wait goes back up: the server restarts the limit on every early request. INFO when there is no stated wait to compare with. |
| `behaviour.recovery` | Recovery | PASS when a client that waits as told (or backs off, with no headers) gets back in at the first try. WARN when it took several tries. FAIL when it never got back in. |
| `behaviour.reset` | Reset | Whether the full burst was available again after the wait, or only part of it. |
| `run.stopped` | Stopped early | FAIL for a block (403 or 451), a 503 with Retry-After ("the server signalled overload or rate limiting with 503 instead of 429", with its wait), another 5xx, a redirect, an unexpected status or a connection error. WARN for a wait longer than `--max-wait` or the budget running out. |

## Likely causes

Patterns are tried in this order and the first that fits is reported, with a
next step for the customer.

| Pattern | Label | When |
|---|---|---|
| `blocked` | the server started blocking | 403 or 451 after 429s |
| `503-as-limit` | 503 used as a rate limit | 503 with Retry-After |
| `long-lockout` | a long lockout or quota | the server asked for a wait longer than `--max-wait` |
| `incomplete` | the probe ran out of requests | the budget ran out |
| `redirect` | the URL redirects | a 3xx, never followed |
| `tls` | the certificate was not trusted | HTTPS verification failed; use `--cacert` for a private CA |
| `connection` | the connection failed | refused, reset or timed out |
| `unauthorised` | the request is not authorised | 401; pass a test key with `--header` |
| `stopped` | the probe stopped early | any other unexpected status |
| `early-retries-extend` | client retries during the limit | early retries restart the wait: a client that retries immediately and ignores Retry-After never leaves the limit |
| `retry-after-too-short` | Retry-After is too short | waiting as told was refused again; an API-side fault |
| `quota-headers-wrong` | the quota headers are wrong | Remaining or Limit contradicts what happened |
| `no-headers` | no rate-limit headers | nothing says how long to wait; the back-off that worked |
| `retry-after-missing` | no Retry-After on 429 | the wait is only in X-RateLimit-Reset or RateLimit |
| `limit-not-reached` | limit not reached | no 429 within the budget |
| `server-behaves` | the limit works as advertised | no failures: the 429s most likely come from the client, and the next step says what to look for in its logs |
