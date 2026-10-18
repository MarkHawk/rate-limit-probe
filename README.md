# rate-limit-probe

Explain, in plain English, why an integration keeps getting
`HTTP 429 Too Many Requests`. rate-limit-probe sends a small, slow, capped
sequence of requests to an API you own or are authorised to test, and reports
when the 429s start, the limit and window it observed, how the limit resets,
whether the rate-limit headers agree with what actually happened, and whether
a client that honours `Retry-After` gets back in. It ends with a likely cause
and a next step for the customer.

It is a diagnostic, not a load or stress tool: one connection, at most 10
requests a second, at most 500 requests a run, localhost only unless you say
otherwise, and it stops at the first sign of a block.

## Try it in 1 minute

From a clone, with Python 3.12 or newer:

```
python3 -m venv venv
source venv/bin/activate
pip install -e .
```

Then watch it diagnose ten rate-limit behaviours on a bundled test server
(about 30 seconds, all on 127.0.0.1):

```
rate-limit-probe demo
```

```
rate-limit-probe demo: 10 behaviours on a local test server, probed one after another

  fixed-seconds    fixed window, Retry-After seconds    PASS  5 per 2 s, headers consistent
  sliding-date     sliding window, Retry-After date     PASS  5 per 2 s, headers consistent
  bucket-ietf      token bucket, IETF RateLimit         PASS  bursts, then refills gradually; headers consistent
  xrl              fixed window, X-RateLimit-*          WARN  likely cause: no Retry-After on 429
  none             fixed window, no headers             WARN  likely cause: no rate-limit headers
  lying            Retry-After shorter than reality     FAIL  likely cause: Retry-After is too short
  stale-remaining  Remaining never counts down          FAIL  likely cause: the quota headers are wrong
  penalty          early retries restart the limit      FAIL  likely cause: client retries during the limit
  block            403 after repeated 429s              FAIL  likely cause: the server started blocking
  overload-503     503 with Retry-After instead of 429  FAIL  likely cause: 503 used as a rate limit

Full report for one behaviour:  rate-limit-probe demo <name>   e.g. rate-limit-probe demo penalty
Run the test server yourself:   rate-limit-probe serve
Probe your own API:             rate-limit-probe <url>
```

### One full report

```
rate-limit-probe demo penalty
```

```
rate-limit-probe 0.1.0
Target:   http://127.0.0.1:41237/s/penalty (local)
Limits:   up to 100 requests, 10/s or slower, one connection, waits up to 60 s
Sent:     13 requests (GET) over 7.0 s
Note:     demo window shortened to 4 s

== Timeline ==
  #1-#5    200  0.0-0.4 s    Remaining 4, 3, 2, 1, 0
  #6       429  0.5 s        Retry-After: 4
                             waited 2 s, to test an early retry
  #7       429  2.5 s        Retry-After: 4; sent early on purpose
                             waited 4 s, as Retry-After asked
  #8       200  6.5 s
  #9-#12   200  6.6-6.9 s    Remaining 3, 2, 1, 0
  #13      429  7.0 s        Retry-After: 4

== Findings ==
  INFO  When 429s start      Request 6, 0.5 s in: 5 requests were accepted first.
  INFO  Observed limit       5 requests, then refused until 4.5 s after the first request.
                             Consistent with a fixed or sliding window of 5 per 4 s, which the Limit
                             header (5) confirms.
  PASS  Retry-After          Sent on every 429, in seconds. Waiting as asked was enough: the next
                             request was accepted.
  PASS  X-RateLimit headers  Remaining counted down to 0, Limit matched what happened and Reset
                             agreed with Retry-After.
  FAIL  Early retry          A request sent 2 s early was refused and the wait went back up to 4 s:
                             the server restarts the limit on every early request. A client that
                             retries before the wait is over stays limited for as long as it keeps
                             retrying.
  PASS  Recovery             A client that waits as told gets back in: accepted 6 s after the first
                             429, at the first try.
  INFO  Reset                After the wait, the full 5 requests were available again at once.

== Likely cause ==
  Each request sent during the limit restarts it, so a client that retries immediately and ignores
  Retry-After never leaves the limit.
  Next step:  Make the client wait for the full Retry-After before retrying, and back off when there
              is none. Check its logs for retries less than 4 s apart after a 429.

Verdict: 1 problem to fix (1 FAIL, 0 WARN, 3 PASS, 3 INFO)
```

The timeline shows exactly what was sent and what came back. The probe sent
request 7 early on purpose, and the server restarted its 4 s wait instead of
counting down, which is the finding that explains the ticket.

## Probing your own API

Only `localhost` and loopback addresses are probed by default. For any other
host, add `--i-am-authorised` to confirm that you own the API or are
authorised to test it; the probe then prints a warning naming the host and
the limits it will keep to.

> **Probing uses up the API key's own quota.** Every request the probe sends
> counts against the key it uses, the same as the customer's own traffic.
> Use a test key or a test environment, never the customer's production key.

```
# an API on this machine
rate-limit-probe http://localhost:8080/v1/items

# a remote test environment, with a test key
rate-limit-probe https://api.example.com/v1/items --i-am-authorised \
    --header 'Authorization: Bearer <test key>'

# a server with a certificate from a private CA
rate-limit-probe https://api.test.internal/v1/items --i-am-authorised --cacert private-ca.pem

# a report safe to paste into a ticket, or one for a script
rate-limit-probe http://localhost:8080/v1/items --redact
rate-limit-probe http://localhost:8080/v1/items --json
```

A run takes from a few seconds to about a minute, because the probe waits as
long as the server asks. Exit status is 0 when nothing failed, 1 when at
least one finding is a FAIL, and 2 when the target could not be reached or
the command was refused. Every flag is in [`docs/usage.md`](docs/usage.md).

## What it finds

| Pattern | What the report says |
|---|---|
| Early retries restart the limit | a client that retries immediately and ignores Retry-After never leaves the limit |
| Retry-After shorter than the real wait | a well-behaved client is refused again and again; an API-side fault |
| Quota headers that do not match | Remaining never counts down, or says requests are left when they are not |
| No Retry-After, or no headers at all | the client has to guess; how long the limit actually lasted |
| 503 with Retry-After instead of 429 | the server signalled overload or rate limiting with 503, and the wait it asked for |
| 403 after repeated 429s | the server moved from limiting to blocking |
| A limit that works as advertised | the 429s come from the client: what to look for in its logs |

It reads `Retry-After` (seconds or an HTTP date), `X-RateLimit-Limit`,
`-Remaining` and `-Reset` (seconds or a Unix time), and the IETF `RateLimit` and
`RateLimit-Policy` headers (and their earlier `RateLimit-Limit` form). Every
finding and likely cause is described in [`docs/findings.md`](docs/findings.md).

## Safety

These limits are constants in [`safety.py`](src/rate_limit_probe/safety.py),
enforced in code, with no flag to remove them:

| Limit | Default | Ceiling |
|---|---|---|
| Requests per run (`--max-requests`) | 100 | 500 |
| Requests per second (`--rate`) | 10 | 10, it can only be lowered |
| Longest single wait (`--max-wait`) | 60 s | 300 s |

- One connection, one request at a time. GET or HEAD only, so a probe never
  changes data.
- No evasion of any kind: a fixed, honest `User-Agent`, no IP or header
  rotation, and `--header` refuses `User-Agent`, `X-Forwarded-For` and the like.
- It stops at the first sign of a block: 403 or 451, any 5xx, a redirect
  (never followed), a connection reset, or a wait longer than allowed.
- HTTPS always verifies the certificate and host name. `--cacert` chooses a
  private CA; verification cannot be turned off.
- Query string values are always masked in reports, and `--header` values
  are never shown.

## Reproducing a ticket with the test server

The demo's server can also run on its own, so you can point the probe, or the
customer's client, at a known behaviour:

```
rate-limit-probe serve
rate-limit-probe http://127.0.0.1:8429/s/penalty
```

It listens on 127.0.0.1 only. Every behaviour is at `/s/<name>`, and
`--algorithm`, `--limit`, `--window` and `--headers` build a custom one at
`/custom`, for example a token bucket of 20 per minute with IETF headers:

```
rate-limit-probe serve --algorithm token-bucket --limit 20 --window 60 --headers ietf,retry-after-seconds
```

## Related tools

- [token-inspector](https://github.com/MarkHawk/token-inspector): an offline
  SAML and OIDC troubleshooting tool that explains what is wrong with a token,
  with a likely cause and next step in the same style.
- [identity-lab](https://github.com/MarkHawk/identity-lab): a Docker identity
  lab that reproduces SAML, OIDC, LDAP and RADIUS failures on demand, each
  with a support runbook.

## Documentation

- [`docs/usage.md`](docs/usage.md) every option of the probe, `serve` and
  `demo`, exit codes and the JSON output.
- [`docs/findings.md`](docs/findings.md) every finding and likely cause, and
  what a pass, warning or failure means.
- [`docs/design.md`](docs/design.md) why the tool is built the way it is,
  the probe sequence, and ideas not yet built.

## Development

```
python3 -m venv venv
venv/bin/pip install -r requirements-dev.txt
venv/bin/pip install -e . --no-deps
venv/bin/pytest
```

The tool itself uses only the Python standard library. The tests reach the
bundled server on loopback and nothing else: a fixture fails any test that
resolves a name or connects anywhere else. They run on a fake clock, so the
suite takes seconds, and the HTTPS tests make a throwaway CA in a temporary
directory.

## A note on how this was built

rate-limit-probe was written with AI-assisted tooling (Claude Code). The
design decisions, the checks and this documentation were reviewed by a
person.

## Licence

MIT. See [LICENSE](LICENSE).
