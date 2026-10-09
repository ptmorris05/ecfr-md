# cfr-md

Download Code of Federal Regulations text from the [eCFR API](https://www.ecfr.gov/developers/documentation/api/v1) as Markdown, from Python or the command line. Every API endpoint is also available as a typed SDK method and CLI command.

## Markdown

Choose between two output layouts:

- **`file`** (the default) produces one Markdown document. The CFR hierarchy is encoded as headings, relative to what you request: a part starts at `#`, its subparts are `##`, and so on.
- **`tree`** produces nested directories. Each hierarchy level gets an `index.md` (heading, authority and source notes, plus links to its children), and each section or appendix gets its own file.

```python
from cfr_md import ECFR

with ECFR() as ecfr:
    text = ecfr.markdown(45, part="164")  # str
    ecfr.save(45, "hipaa.md", part="164")  # one file
    ecfr.save(45, "hipaa/", part="164", layout="tree")  # directory tree
    files = ecfr.markdown_tree(45, part="164")  # {PurePosixPath: str}
    old = ecfr.markdown(45, section="164.502", date="2020-01-01")  # point in time
```

```sh
cfr-md markdown 45 --part 164                     # print to stdout
cfr-md markdown 45 --part 164 -o hipaa.md
cfr-md markdown 45 --part 164 --layout tree -o hipaa/
```

Hierarchy filters are `subtitle`, `chapter`, `subchapter`, `part`, `subpart`, `section` and `appendix`. When `date` is omitted, the client uses the title's most recent available date.

## API coverage

The SDK and CLI follow the API's three services, with one method or command per endpoint:

| SDK | CLI | Endpoint |
| --- | --- | --- |
| `ecfr.admin.agencies()` | `admin agencies` | `/api/admin/v1/agencies.json` |
| `ecfr.admin.corrections(...)` | `admin corrections` | `/api/admin/v1/corrections.json` |
| `ecfr.admin.title_corrections(t)` | `admin title-corrections` | `/api/admin/v1/corrections/title/{t}.json` |
| `ecfr.search.results(q, ...)` | `search results` | `/api/search/v1/results` |
| `ecfr.search.count(q, ...)` | `search count` | `/api/search/v1/count` |
| `ecfr.search.summary(q, ...)` | `search summary` | `/api/search/v1/summary` |
| `ecfr.search.daily_counts(q, ...)` | `search daily-counts` | `/api/search/v1/counts/daily` |
| `ecfr.search.title_counts(q, ...)` | `search title-counts` | `/api/search/v1/counts/titles` |
| `ecfr.search.hierarchy_counts(q, ...)` | `search hierarchy-counts` | `/api/search/v1/counts/hierarchy` |
| `ecfr.search.suggestions(q, ...)` | `search suggestions` | `/api/search/v1/suggestions` |
| `ecfr.versioner.titles()` | `versioner titles` | `/api/versioner/v1/titles.json` |
| `ecfr.versioner.ancestry(t, ...)` | `versioner ancestry` | `/api/versioner/v1/ancestry/{date}/title-{t}.json` |
| `ecfr.versioner.structure(t)` | `versioner structure` | `/api/versioner/v1/structure/{date}/title-{t}.json` |
| `ecfr.versioner.versions(t, ...)` | `versioner versions` | `/api/versioner/v1/versions/title-{t}.json` |
| `ecfr.versioner.full(t, ...)` | `versioner full` | `/api/versioner/v1/full/{date}/title-{t}.xml` |

JSON endpoints return pydantic models, and fields added to the API later are kept in `model.model_extra`. In the CLI, JSON endpoints print the API's JSON and `versioner full` prints the raw XML. Run `cfr-md COMMAND --help` for every option.

## Configuration

Use `Settings` in Python. On the command line, use options placed before the command, or `CFR_MD_*` environment variables:

```python
from cfr_md import ECFR, Settings

ECFR(
    Settings(
        user_agent="my-app/1.0 (me@example.com)",
        headers={"From": "me@example.com"},
        timeout=60,
        max_attempts=5,
        backoff_initial=1,
        backoff_max=60,
        rate_limit=2,
    )
)
```

```sh
cfr-md --user-agent "my-app/1.0" --headers.From me@example.com --rate-limit 2 markdown 45
CFR_MD_USER_AGENT="my-app/1.0" CFR_MD_RATE_LIMIT=2 cfr-md markdown 45
```

Retries (via tenacity) cover connection failures, timeouts, 429 and 5xx responses. They use exponential backoff with jitter and honour `Retry-After`. `rate_limit` caps requests per second across threads.

## Errors

Every exception derives from `cfr_md.ECFRError`:

| Exception | When |
| --- | --- |
| `InvalidParameterError` | An argument fails validation before any request is sent (also a `ValueError`). |
| `TitleNotFoundError` | The title doesn't exist or is reserved. |
| `ECFRConnectionError` / `ECFRTimeoutError` | The API is unreachable, after retries. |
| `APIError` → `BadRequestError`, `NotFoundError`, `RateLimitError`, `ServerError` | An HTTP error. Carries `status_code`, `url`, the server's `detail` message and `retry_after`. |
| `ResponseFormatError` | The response doesn't match the expected JSON or XML. |

CLI exit codes are `0` for success, `1` for API, network or output errors, and `2` for invalid usage.

## Development

```sh
uv sync
uv run pytest             # offline tests
uv run pytest -m network  # live smoke tests against ecfr.gov
uv run ruff format && uv run ruff check
```
