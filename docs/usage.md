# Usage

```
rate-limit-probe <url> [options]      probe a URL
rate-limit-probe serve [options]      run the bundled test server on 127.0.0.1
rate-limit-probe demo [name]          probe every bundled behaviour, one line each
```

## Probing a URL

```
rate-limit-probe http://localhost:8080/v1/items
```

The URL must be `http://` or `https://`. Only `localhost` and loopback
addresses (`127.0.0.0/8`, `::1`) are probed by default, judged on the host as
written in the URL, never on what it resolves to. A URL with a user name or
password in it is refused; send credentials with `--header`.

| Option | What it does |
|---|---|
| `--i-am-authorised` | Allow a host other than localhost. Use it only for an API you own or are authorised to test. The probe prints a warning to standard error naming the host and its limits, and reminding you that every request uses up the API key's own quota, so use a test key or a test environment. |
| `--max-requests N` | Most requests to send. Default 100; anything above 500 is lowered to 500 and the report says so. |
| `--rate PER_SECOND` | Most requests per second. Default 10, which is also the ceiling: it can only be lowered. |
| `--max-wait SECONDS` | The longest single wait the probe will make when the server asks it to wait. Default 60, ceiling 300. A longer `Retry-After` stops the run rather than being waited out. |
| `--method GET\|HEAD` | GET (the default) or HEAD. Nothing that could change data is ever sent. |
| `--header 'NAME: VALUE'` | Send this header on every request, for example a test key. Repeat for more. The values never appear in the report or the JSON. `User-Agent`, `Host`, `X-Forwarded-For`, `Forwarded` and similar headers are refused, because the probe always identifies itself honestly. |
| `--cacert FILE` | For `https://`, trust this PEM CA certificate instead of the system's, for a server whose certificate comes from a private CA. Certificate and host name verification are always on; there is no option to turn them off. |
| `--json` | Print the report as JSON instead of text. |
| `--redact` | Mask the host and the path wherever they appear in the report, so it can go into a ticket without naming the API. Query string values are masked in every report, with or without this flag. |
| `--version` | Print the version. |

Progress (and the warning for a non-local host) goes to standard error; the
report goes to standard output.

### What the probe sends

1. **Discover.** Requests at the paced rate until the first 429, keeping up to
   20 requests back for the later phases.
2. **Early retry.** One request halfway through the wait the server asked for
   (or 1 s after the 429, if it gave none), to see whether an early retry is
   refused and whether it extends the limit.
3. **Honour.** Wait exactly as long as the server asks (`Retry-After`, else a
   quota reset header when nothing is left, else a back-off of 1, 2, 4, 8 s and
   so on), then one request. Repeated up to 8 times until a request is accepted.
4. **Refill.** Requests until the next 429, to see whether the whole limit came
   back at once or only part of it.

It stops at once on any status other than 2xx or 429, on a connection error,
on a wait longer than `--max-wait`, or when the request budget is used up.

### Exit status

| Status | Meaning |
|---|---|
| 0 | No finding is a FAIL. |
| 1 | At least one finding is a FAIL. |
| 2 | The command was refused (a non-local host without `--i-am-authorised`, a bad flag) or the target could not be reached at all. |

### JSON output

`--json` prints one object:

| Key | Contents |
|---|---|
| `tool`, `version` | `rate-limit-probe` and its version |
| `target` | `url` (query values masked), `local`, `method` |
| `limits` | `max_requests`, `rate`, `max_wait` actually used |
| `notes` | flags that were lowered to a ceiling |
| `sent`, `duration` | requests sent and seconds taken |
| `stop` | why the run stopped early, or `null` |
| `timeline` | every request (`n`, `phase`, `at`, `status`, `rate_headers`, `retry_after`, `limit`, `remaining`, `reset`, `error`) and every wait (`at`, `seconds`, `source`, `purpose`), in order |
| `findings` | `id`, `status`, `title`, `summary` and `details` for each finding |
| `likely_cause` | `pattern`, `label`, `summary`, `next_step` |
| `summary` | counts of each status, the `verdict` line and the `exit_code` |

Times are seconds since the first request. Finding ids and cause patterns are
stable; the wording of summaries may change.

## The test server: `serve`

```
rate-limit-probe serve
```

Runs every behaviour below at `http://127.0.0.1:8429/s/<name>` until Ctrl+C,
logging each request to standard error. It listens on 127.0.0.1 only and has
no option to listen anywhere else.

| Option | What it does |
|---|---|
| `--port PORT` | The port on 127.0.0.1. Default 8429. |
| `--window SECONDS` | Use this window for every endpoint instead of each behaviour's own 10 s. |
| `--algorithm NAME` | Also serve a custom endpoint at `/custom`: `fixed-window`, `sliding-window` or `token-bucket`. |
| `--limit N` | Requests per window for `/custom`. Default 5. |
| `--headers STYLES` | Comma-separated header styles for `/custom`: `retry-after-seconds`, `retry-after-date`, `x-ratelimit`, `ietf`, `none`. Default `retry-after-seconds`. |
| `--penalty` | `/custom` restarts the limit on every request sent while limited. |
| `--quiet` | Do not log each request. |

### Behaviours

Each allows 5 requests per 10 s window (the demo shortens the window).

| Name | Behaviour |
|---|---|
| `fixed-seconds` | Fixed window, `Retry-After` in seconds. |
| `sliding-date` | Sliding window, `Retry-After` as an HTTP date. |
| `bucket-ietf` | Token bucket, IETF `RateLimit` and `RateLimit-Policy`, plus `Retry-After`. |
| `xrl` | Fixed window, `X-RateLimit-*` only, no `Retry-After`. |
| `none` | Fixed window, no rate-limit headers at all. |
| `lying` | `Retry-After` is a fifth of the real wait. |
| `stale-remaining` | `X-RateLimit-Remaining` never counts down. |
| `penalty` | Every request sent while limited restarts the wait. |
| `block` | After two 429s, everything gets 403. |
| `overload-503` | Refuses with 503 and `Retry-After` instead of 429. |

## The demo: `demo`

```
rate-limit-probe demo            # every behaviour, one line each
rate-limit-probe demo penalty    # the full report for one
rate-limit-probe demo lying --json
```

Starts the test server in-process on a random local port and probes each
behaviour in turn over the real network stack and clock, with windows
shortened to 2 s (4 s for `penalty`, 5 s for `lying`, which need whole-second
waits to show). The whole demo takes about 30 seconds.

| Option | What it does |
|---|---|
| `--json` | With a name, print that behaviour's report as JSON. |
