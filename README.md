# ecfr-md

Fetch U.S. federal regulations (CFR) from the [eCFR API](https://www.ecfr.gov/developers/documentation/api/v1) as Markdown or Python objects.

## Install

ecfr-md isn't on PyPI; install it from GitHub. It needs Python 3.12 or newer, and [git](https://git-scm.com/downloads). [uv](https://docs.astral.sh/uv/) installs Python for you, so it's the easiest way to start from nothing.

**With uv** (Linux and macOS):

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv; then open a new terminal

uv tool install git+https://github.com/ptmorris05/ecfr-md
ecfr-md get 45 CFR 164.502 -o 164.502.md
```

To try it without installing: `uvx --from git+https://github.com/ptmorris05/ecfr-md ecfr-md get 45 CFR 164.502`.

**With pipx:**

```sh
brew install pipx              # macOS (Homebrew)
sudo apt install pipx          # Debian, Ubuntu
sudo dnf install pipx          # Fedora
pipx ensurepath                # then open a new terminal

pipx install git+https://github.com/ptmorris05/ecfr-md
ecfr-md get 45 CFR 164.502 -o 164.502.md
```

pipx uses your system Python; if that's older than 3.12, use uv, or add `--python python3.12` if you have Python 3.12 installed.

These install the latest code on `main`. For a release, add its tag: `git+https://github.com/ptmorris05/ecfr-md@v0.1.0`.

To update: `uv tool upgrade ecfr-md` or `pipx upgrade ecfr-md`.

For use from Python:

```sh
uv add git+https://github.com/ptmorris05/ecfr-md
pip install git+https://github.com/ptmorris05/ecfr-md
```

## Command line

```sh
ecfr-md get 45 CFR 164.502                       # print a section
ecfr-md get "45 CFR 164.502(a)(1)"               # a paragraph (quote parentheses)
ecfr-md get 45 CFR Part 164 -o hipaa.md          # save to one file
ecfr-md get 45 CFR Part 164 --tree -o hipaa/     # one file per section
ecfr-md get 45 CFR 164.502 --date 2020-01-01     # as of a date
ecfr-md get --from list.txt -o out/              # one citation per line

ecfr-md search "minimum necessary"               # sections that use the phrase
ecfr-md search "minimum necessary" --save out/   # and save their text
ecfr-md outline 45 CFR Part 164 --depth 1        # table of contents, no text
ecfr-md changes 45 CFR Part 164 --since 2024-01-01
ecfr-md sync hipaa/                              # update saved files

ecfr-md cache info
ecfr-md cache clear
```

| Command | Does |
| --- | --- |
| `get CITATION` | Prints Markdown, or saves it with `-o FILE`. `--tree -o DIR` writes one file per section. `--from FILE -o DIR` takes one citation per line (`-` for stdin). `--date` for text as of a date. `--no-front-matter` leaves out the metadata header. |
| `outline CITATION` | Prints the table of contents. `--depth N` limits levels and counts what's below. Part sizes are shown. |
| `changes CITATION` | Lists changes to sections: when issued, when effective, and whether substantive, editorial or a removal. `--since` and `--until` take dates. |
| `search QUERY` | Lists current sections that contain the words as a phrase (`--no-phrase`: anywhere in the section). `--limit N` (default 20), `--all-versions`, `--save DIR`, `--json`, plus the API's filters (`--agency SLUG`, `--date`, `--last-modified-after`, …). |
| `sync PATH` | Updates a file, tree or directory saved by `get`, `search --save` or `--from`. Rewrites only files whose text changed, deletes files for sections that no longer exist, and leaves other files alone. |
| `cache info`, `cache clear` | Shows or deletes the local cache. |
| `api admin …`, `api search …`, `api versioner …` | One command per API endpoint, printing its JSON. See [API](#api). |

`outline`, `changes` and `search` take `--json` for JSON output. A citation can be one quoted string or separate words; keywords such as `--part 164` work too. Without `-o`, `get` prints formatted text in a terminal and plain Markdown when piped.

Options that go before the command (`ecfr-md --help` lists them all):

| Option | Environment variable | Default |
| --- | --- | --- |
| `-q`, `--quiet`: no progress or summary, only warnings and errors | `ECFR_MD_QUIET` | off |
| `-v`, `--verbose`: log every request | `ECFR_MD_VERBOSE` | off |
| `--no-cache`, `--cache-dir PATH`, `--cache-max-age SECONDS` | `ECFR_MD_CACHE`, `ECFR_MD_CACHE_DIR`, `ECFR_MD_CACHE_MAX_AGE` | on, user cache directory |
| `--user-agent TEXT`, `--headers.NAME VALUE` | `ECFR_MD_USER_AGENT`, `ECFR_MD_HEADERS` | `ecfr-md/<version>` |
| `--rate-limit N`: requests per second | `ECFR_MD_RATE_LIMIT` | none |
| `--concurrency N`: parts fetched at once | `ECFR_MD_CONCURRENCY` | 4 |
| `--timeout`, `--max-attempts`, `--backoff-initial`, `--backoff-max` | `ECFR_MD_…` | 60 s, 5, 1 s, 60 s |

While it runs, `ecfr-md` shows on stderr what it is fetching and as of which date, a bar per download, progress through parts for large titles, retries and rate limiting, and a summary at the end.

Exit codes: `0` success, `1` API, network or file error, `2` invalid usage.

`ecfr-md COMMAND --help` lists a command's options.

## Python

```python
import ecfr_md

section = ecfr_md.get("45 CFR 164.502")
section["(a)(1)"].text  # one paragraph, as Markdown
section.save("164.502.md")  # returns the path

ecfr_md.get("45 CFR 164.502", date="2020-01-01")  # as of a date
ecfr_md.save("45 CFR Part 164", "hipaa/", tree=True)  # one file per section

for match in ecfr_md.search("minimum necessary", limit=5):
    print(match.citation, match.heading)

for change in ecfr_md.changes("45 CFR Part 164", since="2024-01-01"):
    print(change.issued, change.citation, change.kind)

print(ecfr_md.outline("45 CFR Part 164")["Subpart E"])  # table of contents
ecfr_md.sync("hipaa/")  # update saved files
```

The module-level functions share one client with default settings. To set a user agent, rate limit and so on, or to close connections when you're done, make your own:

```python
from ecfr_md import ECFR, Settings

with ECFR(Settings(user_agent="my-app/1.0 (me@example.com)")) as ecfr:
    section = ecfr.get("45 CFR 164.502")  # same methods as the module
```

- Citations down to the paragraph: `45 CFR 164.502(a)(1)(i)`
- Text as of any date the eCFR has (2017 on)
- All text from the source is kept; a nightly test checks this
- Saved files record the citation, date, source URL and retrieval time
- Typed methods for every eCFR API endpoint

## Citations

| Citation | Gets |
| --- | --- |
| `45`, `Title 45` | Title |
| `45 CFR Subtitle A Chapter I Subchapter C` | Subtitle, chapter, subchapter |
| `45 CFR 164`, `45 CFR Part 164` | Part |
| `45 CFR Part 164 Subpart E` | Subpart |
| `45 CFR 164.502`, `45 C.F.R. § 164.502` | Section |
| `45 CFR 164.502(a)(1)(i)(A)` | Paragraph |
| `45 CFR Appendix A to Subpart C of Part 164`, `12 CFR Supplement I to Part 1026` | Appendix, supplement, table, exhibit, etc. |

Case, `§`, commas and periods in "C.F.R." don't matter. Keywords also work, alone or with a citation:

```python
ecfr_md.get(45, part="164", subpart="E")
```

```sh
ecfr-md get 45 --part 164 --subpart E
```

In a shell, quote citations that contain parentheses.

## Dates

Without a date you get the latest text. Dates can be `date` or `datetime` objects, objects with a `.date()` method, or strings such as `2020-01-01`, `Jan 1 2020`, `1/2/2020` (month first), `March 2020` (the 1st), `2020` (January 1) or `last year`. Invalid ISO dates such as `2020-13-01` raise an error. A date with no text raises `DateOutOfRangeError`, which gives the available range.

## Saving

```python
ecfr_md.save("45 CFR Part 164", "hipaa.md")  # one file; returns its path
ecfr_md.save("45 CFR Part 164", "hipaa/", tree=True)  # directories; returns the files
```

```sh
ecfr-md get 45 CFR Part 164 -o hipaa.md
ecfr-md get 45 CFR Part 164 --tree -o hipaa/
```

- One file: headings follow the hierarchy, starting at `#` for what you asked for. If the path is an existing directory, the file is named after the citation, e.g. `45-cfr-164.502.md`.
- Tree: a directory per level, each with an `index.md` (heading, notes, links to children), and one file per section or appendix.

Each file starts with YAML front matter. Turn it off with `front_matter=False` or `--no-front-matter`.

```yaml
---
citation: "45 CFR 164.502"
heading: "§ 164.502 Uses and disclosures of protected health information: General rules."
date: "2026-10-07"
source: "https://www.ecfr.gov/on/2026-10-07/title-45/section-164.502"
api: "https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/title-45.xml?section=164.502"
retrieved: "2026-10-09T17:02:11+00:00"
generator: "ecfr-md 0.1.0"
disclaimer: "The eCFR is an editorial compilation of CFR material and Federal Register amendments. It is not an official legal edition of the CFR."
---
```

`save` processes the XML as it reads it from disk, so large titles don't need to fit in memory. The API times out on the largest titles (e.g. Title 40), so text above the part level over 50 MB is fetched one part at a time and joined. Files are written to a temporary name and renamed.

## Navigating

`get` makes one request and returns a node (`Title`, `Subtitle`, `Chapter`, `Subchapter`, `Part`, `Subpart`, `SubjectGroup`, `Section`, `Appendix` or `Paragraph`). Navigating it makes no further requests.

```python
part = ecfr_md.get("45 CFR Part 164")

for subpart in part:  # children
    print(subpart.identifier, subpart.heading)

part[0]  # first child
part["Subpart E"], part["§ 164.512"]  # by identifier, at any depth
part["164.512(k)(1)(i)"]  # paragraphs
part["45 CFR 164.512(k)(1)"]  # citations
part.get("164.999")  # None if missing
part.iter("section")  # all sections
part.find(level="appendix")  # first match

paragraph = part["164.512(k)(1)(i)"]
paragraph.citation  # '45 CFR 164.512(k)(1)(i)'
paragraph.heading  # 'Armed Forces personnel'
paragraph.text  # its own Markdown
paragraph.parent, paragraph.section, paragraph.ancestors
```

Nodes have `level`, `identifier`, `heading`, `citation`, `hierarchy`, `date`, `provenance`, `parent`, `children`, `to_markdown()`, `to_markdown_tree()` and `save()`.

If more than one node matches a key, `node[key]` raises `AmbiguousNodeError` with the matches in `.candidates`. `find()` returns the first match.

### Outlines, changes and search results

`outline()` returns the table of contents as an `Outline`, without the text. It navigates like a node (`outline["Subpart E"]`, `outline.iter("section")`, `.parent`, `.children`) and has `level`, `identifier`, `heading`, `citation`, `reserved` and `size`; `str(outline)` is the indented table of contents.

`changes()` returns `Change` objects, newest first: `citation`, `heading`, `issued` (when the eCFR published it), `effective` and `kind` (`"substantive"`, `"editorial"` or `"removed"`).

`search()` returns `Match` objects, best first: `citation` (pass it to `get`), `heading`, `excerpt`, `starts_on` and `ends_on`. The list's `.total` is how many the eCFR found. Words are matched as a phrase unless `phrase=False` or the query has its own quotes; `limit`, `all_versions` and the API's filters (`agency_slugs`, `date`, `last_modified_after`, …) are keywords.

Each keeps the API's response in `.raw`.

### Paragraphs

The XML lists a section's paragraphs flat. They are nested by marker: (a), (1), (i), (A), then italic (*1*) and (*i*). Markers that could be a letter or a roman numeral, like (i), are decided by what comes before. Also handled:

- run-in children: `(k) Standard—(1) Military—(i) …`
- reserved ranges: `(b)-(d) [Reserved]`
- worked examples (`Example 1. (i) Facts…`), which restart numbering and are kept with the paragraph they belong to

Some sections don't follow the numbering rules, such as definitions that number items under each term, and the vehicle safety standards in 49 CFR Part 571, which number paragraphs S1, S4.1 and so on (these aren't split into paragraphs). `Section.paragraph_issues` lists repeats and gaps. `Section.to_markdown()` renders the source as-is either way.

## API

One method and one command per endpoint, under `ecfr.api` and `ecfr-md api`:

| SDK | CLI | Endpoint |
| --- | --- | --- |
| `ecfr.api.admin.agencies()` | `api admin agencies` | `/api/admin/v1/agencies.json` |
| `ecfr.api.admin.corrections(...)` | `api admin corrections` | `/api/admin/v1/corrections.json` |
| `ecfr.api.admin.title_corrections(t)` | `api admin title-corrections` | `/api/admin/v1/corrections/title/{t}.json` |
| `ecfr.api.search.results(q, ...)` | `api search results` | `/api/search/v1/results` |
| `ecfr.api.search.count(q, ...)` | `api search count` | `/api/search/v1/count` |
| `ecfr.api.search.summary(q, ...)` | `api search summary` | `/api/search/v1/summary` |
| `ecfr.api.search.daily_counts(q, ...)` | `api search daily-counts` | `/api/search/v1/counts/daily` |
| `ecfr.api.search.title_counts(q, ...)` | `api search title-counts` | `/api/search/v1/counts/titles` |
| `ecfr.api.search.hierarchy_counts(q, ...)` | `api search hierarchy-counts` | `/api/search/v1/counts/hierarchy` |
| `ecfr.api.search.suggestions(q, ...)` | `api search suggestions` | `/api/search/v1/suggestions` |
| `ecfr.api.versioner.titles()` | `api versioner titles` | `/api/versioner/v1/titles.json` |
| `ecfr.api.versioner.ancestry(t, ...)` | `api versioner ancestry` | `/api/versioner/v1/ancestry/{date}/title-{t}.json` |
| `ecfr.api.versioner.structure(t)` | `api versioner structure` | `/api/versioner/v1/structure/{date}/title-{t}.json` |
| `ecfr.api.versioner.versions(t, ...)` | `api versioner versions` | `/api/versioner/v1/versions/title-{t}.json` |
| `ecfr.api.versioner.full(t, ...)` | `api versioner full` | `/api/versioner/v1/full/{date}/title-{t}.xml` |

JSON endpoints return pydantic models. Fields the API adds later are kept in `model.model_extra`. `ecfr_md.load(xml, title=45)` builds nodes from XML you already have. `ecfr-md COMMAND --help` lists options.


## Fewer requests

ecfr-md avoids requests it doesn't need:

- **Cache, on by default.** Downloaded text is kept in the user cache directory (`~/Library/Caches/ecfr-md` on macOS, `~/.cache/ecfr-md` on Linux) and reused until the eCFR changes it. Every change the eCFR makes, including corrections to past dates, gets an issue date. To check a stored copy, ecfr-md reads the title list (one small request, reused for 5 minutes); only if the title has changed since the copy was made does it request the list of versions issued since, once per title. Text that hasn't changed isn't downloaded again. Titles still being updated aren't stored.
- **Large titles are updated part by part.** When a large title changes, only the parts that changed are downloaded again.
- **Several citations, one request.** `get_many([...])` and `ecfr-md get --from list.txt` fetch sections in the same small part (under 2 MB) with one request.
- **One connection.** Requests share one HTTP/2 connection and ask for gzip, which the API sends about 5× smaller.

```sh
ecfr-md cache info      # where the cache is and its size
ecfr-md cache clear
ecfr-md --no-cache get 45 CFR 164.502
```

```python
nodes = ecfr_md.get_many(["45 CFR 164.502", "45 CFR 164.506", "45 CFR 164.508"])
```

## Configuration

`Settings` in Python; options before the command or `ECFR_MD_*` environment variables on the command line.

```python
from ecfr_md import ECFR, Settings

ECFR(
    Settings(
        user_agent="my-app/1.0 (me@example.com)",
        headers={"From": "me@example.com"},
        timeout=60,
        max_attempts=5,
        backoff_initial=1,
        backoff_max=60,
        rate_limit=None,
        concurrency=4,
        cache=True,
        cache_dir=None,
        cache_max_age=None,
    )
)
```

```sh
ecfr-md --user-agent "my-app/1.0" --rate-limit 2 get 45
ECFR_MD_USER_AGENT="my-app/1.0" ecfr-md get 45
```

- Retries: connection errors, timeouts, 429 and 5xx, with exponential backoff and jitter. `Retry-After` is used when present.
- `rate_limit`: maximum requests per second (default: none). When the API answers 429, the rate is halved and every request pauses; it recovers gradually as requests succeed.
- `concurrency`: parts fetched at once for large titles.
- `cache_dir`: where the cache lives; `cache_max_age` (seconds) forces a fresh download of older copies.
- `ECFRWarning` is issued while the eCFR is updating a title.

## Limitations

- The eCFR is an editorial compilation, not an official legal edition of the CFR. Saved files say so in their front matter.
- Text is available from the eCFR's first versions (late 2016/2017) to its latest date; other dates raise `DateOutOfRangeError`.
- Paragraphs are nested from their markers. Sections that don't follow the usual (a), (1), (i), (A) scheme are listed in `Section.paragraph_issues`; lookups in them may be ambiguous. Examples: numbered items under unmarked definitions, and 49 CFR Part 571, which numbers paragraphs S1, S4.1 and so on (those aren't split into paragraphs).
- The full-text endpoint ignores `subchapter` unless `chapter` is also given.
- Without `-o`, text over 2 MB is printed as plain Markdown, not formatted.

## Public API

Everything exported from `ecfr_md` (the module-level functions, `ECFR`, `Settings`, `Citation`, the node classes, `Level`, `Outline`, `Change`, `Match`, `Matches`, `load`, `Progress`, `SyncResult`, the search enums and typed dicts, and the exceptions), plus the response models in `ecfr_md.models`. Other modules are internal. The `ecfr-md` command and its options are public too.

## Errors

All exceptions derive from `ecfr_md.ECFRError`.

| Exception | Raised when |
| --- | --- |
| `InvalidParameterError` | An argument is invalid. Raised before any request. Also a `ValueError`. |
| `InvalidCitationError` | A citation can't be parsed or conflicts with a keyword. |
| `DateOutOfRangeError` | There is no text for the date. |
| `TitleNotFoundError` | The title doesn't exist or is reserved. |
| `NodeNotFoundError` | A lookup matches nothing. Also a `KeyError`. |
| `AmbiguousNodeError` | A lookup matches more than one node. |
| `ECFRConnectionError`, `ECFRTimeoutError` | The API can't be reached, after retries. |
| `APIError`: `BadRequestError`, `NotFoundError`, `RateLimitError`, `ServerError` | HTTP error. Has `status_code`, `url`, `detail`, `retry_after`. |
| `ResponseFormatError` | A response isn't the expected JSON or XML. |

## Examples

[`examples/`](https://github.com/ptmorris05/ecfr-md/tree/main/examples): [quickstart](https://github.com/ptmorris05/ecfr-md/blob/main/examples/01_quickstart.py), [navigating](https://github.com/ptmorris05/ecfr-md/blob/main/examples/02_navigate.py), [comparing dates](https://github.com/ptmorris05/ecfr-md/blob/main/examples/03_point_in_time.py), [search](https://github.com/ptmorris05/ecfr-md/blob/main/examples/04_search.py), [CLI](https://github.com/ptmorris05/ecfr-md/blob/main/examples/cli.sh).

## Development

```sh
uv sync
uv run pytest             # offline tests
uv run pytest -m network  # tests against ecfr.gov
uv run ruff format && uv run ruff check
```

CI runs on Linux, macOS and Windows with Python 3.12–3.14. A nightly job runs the network tests and opens an issue if they fail. Changes are listed in [CHANGELOG.md](https://github.com/ptmorris05/ecfr-md/blob/main/CHANGELOG.md).
