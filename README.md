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

These install the latest code on `main`. For a release, add its tag (`git+https://github.com/ptmorris05/ecfr-md@v0.1.0`), or install the wheel attached to the [release](https://github.com/ptmorris05/ecfr-md/releases), which needs no git: `uv tool install https://github.com/ptmorris05/ecfr-md/releases/download/v0.1.0/ecfr_md-0.1.0-py3-none-any.whl`. Each release's files carry [build provenance](https://docs.github.com/en/actions/security-for-github-actions/using-artifact-attestations); check one with `gh attestation verify FILE --repo ptmorris05/ecfr-md`.

To update: `uv tool upgrade ecfr-md` or `pipx upgrade ecfr-md`.

For use from Python:

```sh
uv add git+https://github.com/ptmorris05/ecfr-md
pip install git+https://github.com/ptmorris05/ecfr-md
```

Saving Word and PDF files needs an extra: `docx`, `pdf`, or `all` for every extra. Name it before the `@`:

```sh
uv tool install "ecfr-md[all] @ git+https://github.com/ptmorris05/ecfr-md"
uv add "ecfr-md[pdf] @ git+https://github.com/ptmorris05/ecfr-md"
pip install "ecfr-md[docx] @ git+https://github.com/ptmorris05/ecfr-md"
```

PDFs are typeset by [Typst](https://typst.app/), whose Python wheels need no other software. The MCP server for AI agents needs the `mcp` extra (see [AI agents](#ai-agents)); `all` includes it.

## Command line

```sh
ecfr-md get 45 CFR 164.502                       # print a section
ecfr-md get 45/164.502/a/1                       # a paragraph, by its address
ecfr-md get 45/164.502 45/164.504 -o out/        # several, one file each
ecfr-md get 45 CFR Part 164 -o hipaa.md          # save to one file
ecfr-md get 45 CFR Part 164 --tree -o hipaa/     # one file per section
ecfr-md get 45 CFR Part 164 -o hipaa.pdf         # PDF, Word, EPUB, HTML, text, JSON
ecfr-md get 45 CFR 164.502 --format json         # print JSON
ecfr-md get 45 CFR 164.502 --date 2020-01-01     # as of a date
ecfr-md get --from list.txt -o out/              # one citation per line
ecfr-md get --all --tree -o cfr/                 # the whole CFR, every title

ecfr-md titles                                   # what titles exist
ecfr-md search minimum necessary                 # sections that use the phrase
ecfr-md search minimum necessary --in "45 CFR 164"   # only in Part 164
ecfr-md search "minimum necessary" --save out/   # and save their text
ecfr-md outline 45 CFR Part 164 --depth 1        # table of contents, no text
ecfr-md outline 45 CFR 164.502                   # and the sections around it
ecfr-md references 45 CFR 164.502                # what a section cites
ecfr-md cited-by 45 CFR 164.502                  # what cites it (approximate)
ecfr-md changes 45 CFR Part 164 --since 2024-01-01
ecfr-md compare 45 CFR 164.502 --since 2024-04-25    # what changed, as a redline
ecfr-md sync hipaa/                              # update saved files
ecfr-md sync old/ --migrate                      # and rename to today's layout

ecfr-md cache info
ecfr-md cache clear
```

| Command | Does |
| --- | --- |
| `get CITATION` | Prints the text, or saves it with `-o FILE`. In a terminal, paragraphs are indented, a paragraph shows the section and paragraphs above it, and long text goes through a pager (`--no-pager` to skip it); every lookup ends with its citation, date and eCFR link. Several citations or addresses print one after another, or with `-o DIR` save one file each; with no citation in a terminal, it asks for one ("Citation or address:"), so you can paste. `-o` takes a file, a directory (an existing one, or a name ending in `/`) or `-` for stdout; the file's suffix picks the format (`.md`, `.txt`, `.html`, `.json`, `.epub`, `.docx`, `.pdf`), or `--format` does (see [Formats](#formats)). `--tree -o DIR` writes one file per section. `--from FILE -o DIR` takes one citation per line (`-` for stdin; `# comments`, quotes and lists such as `45 CFR 164.502, 164.506` are fine): every line is checked first, repeats are saved once, and if some can't be found the rest are still saved. `--all -o DIR` saves every title (with `--tree`, a folder per title). `--date` for text as of a date. `--no-front-matter` leaves out the metadata header. `--links` makes cross-references Markdown links (on for Markdown `--tree`, where they link the saved files to each other; `--no-links` to turn that off). |
| `titles` | Lists the titles and the latest date of each one's text. |
| `outline CITATION` | Prints the table of contents (no citation: the titles). `--depth N` limits levels and counts what's below. Part sizes are shown. For a section or appendix, ends with the ones before and after it, across parts and titles. |
| `references CITATION` | Lists the cross-references in the text ("§ 164.501", "paragraph (a) of this section", "42 U.S.C. 1320d", ...) and what each refers to. `--json` adds addresses and links. |
| `cited-by CITATION` | Lists sections that may cite a section or part, by searching for its number: approximate. `--limit`, `--date`, `--json`. |
| `changes CITATION` | Lists changes to sections: when issued, when effective, and whether substantive, editorial or a removal. `--since` and `--until` take dates. Ends with the `compare` command for the latest change. |
| `compare CITATION --since DATE` | Shows what changed in the text between `--since` and `--until` (default: latest): changed, added and removed paragraphs, with words struck out and inserted. Piped, it prints Markdown with `<del>` and `<ins>`. |
| `search WORDS` | Lists current sections that contain the words as a phrase (quotes optional; `--no-phrase`: anywhere in the section). `--in CITATION` searches only a title or part of one. `--limit N` (default 20), `--all-versions`, `--save DIR`, `--json`, plus the API's filters (`--agency SLUG`, `--date`, `--last-modified-after`, …); an unknown agency is named, with suggestions. |
| `sync PATH` | Updates a file, tree or directory saved by `get`, `search --save` or `--from`, searching folders to any depth. Rewrites only files whose text changed, deletes files for sections that no longer exist, and leaves other files alone. Keeps snapshots saved with a past `--date` (`--include-dated` updates them) and files you edited (`--force` overwrites them). `--dry-run` shows what would change. See [Keeping files up to date](#keeping-files-up-to-date). |
| `cache info`, `cache clear` | Shows (`--json` too) or deletes the local cache. |
| `api admin …`, `api search …`, `api versioner …` | One command per API endpoint, printing its JSON. See [API](#api). |

`titles`, `outline`, `changes`, `compare`, `search`, `references` and `cited-by` take `--json` for JSON output (`search --json` is `{"complete", "total_versions", "results"}`). A citation can be one quoted string or separate words, and anything in [Citations and addresses](#citations-and-addresses) works, eCFR links and saved files' names included; keywords such as `--part 164` work too. Without `-o`, `get` prints formatted text in a terminal and plain Markdown when piped. When stderr isn't a terminal (a pipe, a log, CI), messages and summaries are plain whole lines you can grep.

A citation that names a subpart or appendix without its part, like `45 CFR Subpart E`, is checked against the title's table of contents: if several parts have one, the error lists them rather than guessing. A citation that doesn't exist says what does ("No subpart Z in 45 CFR Part 164. It has subparts A, B, C, D, E"). A date after the latest the eCFR has gives the latest text, with a warning, and files are labeled with the text's real date.

Options that go before the command (`ecfr-md --help` lists them all):

| Option | Environment variable | Default |
| --- | --- | --- |
| `-q`, `--quiet`: no progress or summary, only warnings and errors | `ECFR_MD_QUIET` | off |
| `-v`, `--verbose`: log every request | `ECFR_MD_VERBOSE` | off |
| `--no-cache`, `--cache-dir PATH`, `--cache-max-age SECONDS` | `ECFR_MD_CACHE`, `ECFR_MD_CACHE_DIR`, `ECFR_MD_CACHE_MAX_AGE` | on, user cache directory |
| `--user-agent TEXT`, `--headers.NAME VALUE` | `ECFR_MD_USER_AGENT`, `ECFR_MD_HEADERS` | `ecfr-md/<version>` |
| `--rate-limit N`: requests per second | `ECFR_MD_RATE_LIMIT` | none |
| `--concurrency N`: most requests in flight at once, across all threads | `ECFR_MD_CONCURRENCY` | 2 |
| `--timeout`, `--max-attempts`, `--backoff-initial`, `--backoff-max` | `ECFR_MD_…` | 60 s, 5, 1 s, 60 s |

While it runs, `ecfr-md` shows on stderr what it is fetching and as of which date, a bar per download, progress through parts for large titles, retries and rate limiting, and a summary at the end.

The eCFR answers "too many requests" (429) when one client has 3 or more requests running, whatever the rate (measured November 2026); 2 run without any and get over 90% of the best throughput. So `--concurrency` defaults to 2 and is enforced across all threads. If a 429 comes anyway, ecfr-md halves the requests in flight, never returns to the level that got it, and logs a warning: a 429 means the settings need tuning, not more retries. 429s don't count against `--max-attempts`.

Exit codes: `0` success, `1` API, network or file error, `2` invalid usage.

`ecfr-md COMMAND --help` lists a command's options.

## Python

```python
import ecfr_md

section = ecfr_md.get("45 CFR 164.502")
section["(a)(1)"].text  # a paragraph and its subparagraphs, as Markdown
section.save("164.502.md")  # returns the path

ecfr_md.get("45 CFR 164.502", date="2020-01-01")  # as of a date
ecfr_md.save("45 CFR Part 164", "hipaa/", tree=True)  # one file per section

for match in ecfr_md.search("minimum necessary", within="45 CFR Part 164"):
    print(match.citation, match.heading)

for change in ecfr_md.changes("45 CFR Part 164", since="2024-01-01"):
    print(change.issued, change.citation, change.kind)

print(ecfr_md.compare("45 CFR 164.502", since="2024-04-25").to_markdown())  # redline

print(ecfr_md.outline("45 CFR Part 164")["Subpart E"])  # table of contents
ecfr_md.titles()  # every title, with the date of its latest text
ecfr_md.sync("hipaa/")  # update saved files

nodes = ecfr_md.get_many(["45 CFR 164.502", "45 CFR 164.999"], errors="return")
# -> [<Section 45 CFR 164.502 ...>, NodeNotFoundError('No section 164.999 ...')]
```

Misspelled keywords are errors, with a suggestion (`get(45, prt="164")`: "did you mean part?"), and levels can be numbers (`part=164`). Anything that doesn't exist raises a `LookupError` (`NodeNotFoundError`, `TitleNotFoundError`, or `AmbiguousNodeError` listing the candidates).

The module-level functions share one client with default settings. To set a user agent, rate limit and so on, or to close connections when you're done, make your own:

```python
from ecfr_md import ECFR, Settings

with ECFR(Settings(user_agent="my-app/1.0 (me@example.com)")) as ecfr:
    section = ecfr.get("45 CFR 164.502")  # same methods as the module
```

- Citations down to the paragraph: `45 CFR 164.502(a)(1)(i)`, or the address `45/164.502/a/1/i`
- Text as of any date the eCFR has (2017 on)
- All text from the source is kept; a nightly test checks this
- Saved files record the citation, date, source URL and retrieval time
- Typed methods for every eCFR API endpoint

## Citations and addresses

Every place in the CFR has an **address**: short, unambiguous, and safe to type in a shell, put in a URL or use as a file name. ecfr-md names its files with them, and `get`, `save`, `outline`, `changes`, `search(within=)` and node lookups take them, along with citations and links.

| Address | Citation | Is |
| --- | --- | --- |
| `45` | `45 CFR` | Title |
| `45/subtitle-A`, `45/chapter-I/subchapter-C` | `45 CFR Subtitle A`, `45 CFR Chapter I Subchapter C` | Subtitle, chapter, subchapter |
| `45/part-164` (or `45/164`) | `45 CFR Part 164` | Part |
| `45/part-164/subpart-E` | `45 CFR Part 164 Subpart E` | Subpart |
| `45/164.502` | `45 CFR 164.502` | Section |
| `45/164.502/a/1/i/A` | `45 CFR 164.502(a)(1)(i)(A)` | Paragraph |
| `12/part-1026/supplement-I`, `45/part-164/subpart-C/appendix-A` | `12 CFR Supplement I to Part 1026`, `45 CFR Appendix A to Subpart C of Part 164` | Appendix, supplement, table (`table-1`), figure, schedule, … |
| `12/part-9/group-ECFR129c8723f2e5dc7` | | Unnumbered group of sections, by its eCFR id |
| `45/164.502@2020-01-01` | | As of a date (also `@2020`, `@2020-01`, `@current`) |

- A section is unique in its title, so its address leaves out the part. Parentheses inside a section number become dots: 26 CFR 1.401(k)-1 is `26/1.401.k-1` (and 26 CFR 1.401 paragraph (k) is `26/1.401/k`). The eCFR's reserved ranges use `..`: `17/210.3-07..210.3-08`.
- Paragraph markers lose their parentheses and italics. Where a marker repeats among its siblings, as in definitions, each says which it is: `45/164.501/1~2` is the second (1); `45/164.501/1` alone is ambiguous and raises `AmbiguousNodeError` listing them.
- Lists and ranges: `45/164.502,164.504` (later items keep the title), `45/164.502/a/1,2` (a marker alone is a sibling of the paragraph before), `45/164.502/b..d`.
- Addresses use only letters, digits and `. - / @ , ~`, in the eCFR's case.

```python
from ecfr_md import Address

address = Address.parse("45 C.F.R. § 164.502(a)(1) (2018)")
str(address)  # '45/164.502/a/1@2018-10-01'
address.citation()  # '45 CFR 164.502(a)(1)'
address.citation("bluebook")  # '45 C.F.R. § 164.502(a)(1) (2018)'
address.url()  # 'https://www.ecfr.gov/on/2018-10-01/title-45/section-164.502#p-164.502(a)(1)'
address.uri()  # 'cfr:45/164.502/a/1@2018-10-01'
address.file_name()  # '45_164.502_a_1@2018-10-01.md'
Address.parse_many("45/164.502,164.504")  # two addresses
```

Nodes have `address` (`45/164.502/a/1`), `uri` (`cfr:45/164.502/a/1@2024-01-01`, with the text's date), `citation` (`45 CFR 164.502(a)(1)`), `cite("bluebook")` and `url` (the eCFR page); `to_dict()` includes `address` and `uri`, as do outlines, changes, matches and titles.

`Address.parse` (and everything that takes a citation) reads:

- Addresses, also as `title-45/section-164.502` or `cfr:45/164.502` URIs.
- Citations, with or without `§`, case, commas and periods in "C.F.R.": `45 CFR 164.502(a)(1)`, `45 C.F.R. pt. 164, subpt. E`, `45 CFR Subtitle A Chapter I Subchapter C`, `45 CFR Appendix A to Subpart C of Part 164`, `12 CFR Supplement I to Part 1026`, markers spaced or dotted (`45 CFR 164.502 a 1`, `45 CFR 164.502.a.1`), and lists (`45 CFR §§ 164.502, 164.506`).
- Links: eCFR pages (`https://www.ecfr.gov/current/title-45/section-164.502#p-164.502(a)(1)`, `/on/2020-01-01/...`) and API requests, Cornell LII (`law.cornell.edu/cfr/text/45/164.502#a_1`), GovInfo (`govinfo.gov/link/cfr/45/164?sectionnum=502`, granule ids such as `CFR-2023-title45-vol2-sec164-502`) and USLM identifiers (`/us/cfr/t45/s164.502/a/1`).
- The names of files ecfr-md saved, today's (`45_164.502_a_1.md`, any path in a saved tree) and ecfr-md 0.x's (`45-cfr-164.502-a.md`, `part-164/subpart-e/section-164.502.md`).

Citations can be pasted as found: surrounding quotes and invisible characters are ignored, `Supp.` and `app.` are completed from the part (`12 C.F.R. pt. 1026, app. J` is `Appendix J to Part 1026`), `45 CFR 164.500 et seq.` is § 164.500, and `45 CFR Subpart E of Part 164` and `Part 164 of Title 45` work.

A trailing year, as in a Bluebook citation (`45 C.F.R. § 164.502 (2018)`), means that year's **annual edition** of the title, which is revised as of January 1 for titles 1–16, April 1 for titles 17–27, July 1 for titles 28–41 and October 1 for titles 42–50: `45 CFR 164.502 (2018)` is the text as of 2018-10-01. A date in the citation and a different `date=` (`--date`) is an error.

A range of sections (`164.500-164.534`, `164.500 through 164.534`) is an error: cite the part or subpart instead. Where one place is expected, a list is an error too; `get_many` (on the command line, several citations with `-o DIR`, or `--from FILE`) takes several. `Citation` still parses citations alone, as before, but has no place for a date, so it rejects a trailing year.

Keywords also work, alone or with a citation:

```python
ecfr_md.get(45, part="164", subpart="E")
```

```sh
ecfr-md get 45 --part 164 --subpart E
```

In a shell, quote citations that contain parentheses, or use the address.

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

- One file: headings follow the hierarchy, starting at `#` for what you asked for. If the path is an existing directory (or ends in `/`), the file is named after the address, with `_` for `/`: `45_164.502.md`, `45_164.502_a_1.md`, `45_part-164_subpart-E.md`. Text as of a past date is named with its date too, e.g. `45_part-164@2018-01-01.md`, so snapshots saved into one directory don't overwrite each other. A file name you give is used as is.
- Tree: a directory per level, each with an `index.md` (heading, notes, links to children), and one file per section or appendix, named as in addresses:

  ```
  title-45/index.md
  title-45/subtitle-A/subchapter-C/part-164/index.md
  title-45/subtitle-A/subchapter-C/part-164/subpart-E/164.502.md
  title-12/chapter-X/part-1026/supplement-I.md
  ```

  The top folder is what you saved (`part-164/` for a part). Unnumbered groups of sections get no folder: their sections are in the subpart's folder, under the group's heading in its `index.md`. An appendix to a subpart is in that subpart's folder. Names that would differ only in case, such as 40 CFR Part 60's subparts `Cc` and `CC`, are told apart with `~2` (`subpart-CC`, `subpart-Cc~2`), so trees work on case-insensitive file systems. A path in a saved tree reads as an address, so `ecfr-md get cfr/title-45/subtitle-A/subchapter-C/part-164/subpart-E/164.502.md` gets that text again; dates are in the front matter, not the path.

  Saving a tree over an earlier save of the same tree (another date, say) removes the files ecfr-md wrote for sections that are no longer there, so the tree never mixes dates. Files ecfr-md didn't write, and files you edited, are kept.

### Links

Trees are saved with their cross-references as Markdown links, so a saved tree can be browsed on GitHub or in Obsidian: "§ 164.501", "subpart E of this part" or "appendix A to this part" link to the file that holds it, relative to the file it's in (`../subpart-E/164.501.md`), and anything outside the tree links to its eCFR page. Citations of the U.S. Code, the Federal Register and public laws link to uscode.house.gov, federalregister.gov and congress.gov. Headings and existing links aren't touched.

```python
ecfr_md.save("45 CFR Part 164", "hipaa/", tree=True)  # linked
ecfr_md.save("45 CFR Part 164", "hipaa/", tree=True, links=False)  # plain
ecfr_md.save("45 CFR Part 164", "hipaa.md", links=True)  # one file, links to the eCFR
part.to_markdown(links=True)
```

```sh
ecfr-md get 45 CFR Part 164 --tree -o hipaa/ --no-links
ecfr-md get 45 CFR 164.502 --links
```

Single files and printed text have no links unless asked for. Saved files record `links` in their front matter, and `sync` keeps it. Trees saved before links existed have no `links` field, so their next `sync` rewrites every file once to add them.

Each Markdown file starts with YAML front matter. Turn it off with `front_matter=False` or `--no-front-matter`.

```yaml
---
citation: "45 CFR 164.502"
address: "45/164.502"
heading: "§ 164.502 Uses and disclosures of protected health information: General rules."
date: "2026-10-07"
source: "https://www.ecfr.gov/on/2026-10-07/title-45/section-164.502"
api: "https://www.ecfr.gov/api/versioner/v1/full/2026-10-07/title-45.xml?part=164"
retrieved: "2026-10-09T17:02:11+00:00"
generator: "ecfr-md 0.1.0"
layout: 2
disclaimer: "The eCFR is an editorial compilation of CFR material and Federal Register amendments. It is not an official legal edition of the CFR."
digest: "sha256:3f1c…"
---
```

- `pinned` (only for text as of a past date) repeats the date you asked for, e.g. `pinned: "2018-01-01"`. `sync` keeps such files at that date.
- `layout` is the version of the file names: 2 is the layout above. Files saved by ecfr-md 0.x have none.
- `links` is whether cross-references are links (always recorded for trees; for single files only when true).
- `digest` is the SHA-256 of everything after the front matter. `sync` uses it to tell whether you edited the file. Files saved by earlier versions have no digest and are synced as before.

### Formats

Besides Markdown, ecfr-md saves plain text, HTML, JSON, EPUB, Word and PDF. The file's suffix picks the format, or `format=` (`--format`) does:

```python
part = ecfr_md.get("45 CFR Part 164")
part.export("hipaa.pdf")  # PDF, from the suffix
part.export("hipaa/", "html", tree=True)  # a page per section
ecfr_md.save("45 CFR Part 164", "hipaa.docx")  # the same, without loading it first
ecfr_md.save("45 CFR 164", "hipaa", tree=True, format="epub")
```

```sh
ecfr-md get 45 CFR Part 164 -o hipaa.epub
ecfr-md get 45 CFR Part 164 --tree -o site/ --format html
ecfr-md get --all --tree -o cfr-pdf/ --format pdf   # a PDF per part, every title
```

| Format | Suffix | What you get |
| --- | --- | --- |
| `markdown` | `.md` | As above. |
| `text` | `.txt` | One line per paragraph, indented by its level; tables in columns. Starts with the same front matter as Markdown. |
| `html` | `.html` | One page with its own styles (light and dark, and print), no scripts. Each division is a `<section>` and each paragraph has the eCFR's id, so `hipaa.html#p-164.502(a)(1)` works as it does on ecfr.gov. Notes are small and grey. |
| `json` | `.json` | The node tree: `level`, `identifier`, `heading`, `citation`, `address`, `url`, `text` (the node's own Markdown, without its children's) and `children`, down to sections, with `provenance` on the top node. |
| `epub` | `.epub` | An EPUB 3 e-book with a table of contents, a page per division. |
| `docx` | `.docx` | A Word document using Word's heading styles, so the navigation pane shows the outline. Needs the `docx` extra. |
| `pdf` | `.pdf` | US Letter pages with a bookmark per heading, a running head and page numbers. Needs the `pdf` extra. |

With `tree=True` (`--tree`), text and HTML are saved like Markdown, a file per section or appendix with an index page per level (HTML pages link to their index and back), while JSON, EPUB, Word and PDF get a file per part, in the same folders: `title-45/subtitle-A/subchapter-C/part-164.pdf`. Either way the text is written as it's read, so a tree of a whole title never needs all of it in memory. Without the extra a format needs, saving raises `MissingDependencyError`, saying what to install.

### Keeping files up to date

`sync` brings saved files to the latest text. It reads the citation from each file's front matter (or a tree's top `index.md`), so it works on a single file, a tree's top folder, or any folder holding saves at any depth.

```sh
ecfr-md sync notes/ --dry-run   # show what would change, write nothing
ecfr-md sync notes/             # update
```

```python
result = ecfr_md.sync("notes/", dry_run=True)
result.changed, result.added, result.removed, result.unchanged
result.kept  # snapshots saved with a past date, left at that date
result.modified  # files edited since they were saved, left as they are
result.moved  # (old, new) paths renamed by migrate=True
```

- Snapshots saved with a past date (`pinned` in the front matter) are kept and reported as "kept at saved date". `include_dated=True` (`--include-dated`) moves them to the latest text.
- Files whose text no longer matches their `digest` are kept and reported as "modified locally", including files for sections that no longer exist. `force=True` (`--force`) overwrites or deletes them.
- Files ecfr-md didn't write are never touched.
- Files and trees saved by ecfr-md 0.x, with lowercase names (`45-cfr-164.502.md`, `part-164/subpart-e/section-164.502.md`), are updated in place under those names. `migrate=True` (`--migrate`) renames them to today's layout (`45_164.502.md`, `part-164/subpart-E/164.502.md`) and lists them in `result.moved` as (old, new) pairs. Names you chose, such as `hipaa.md` or a tree folder you renamed, are kept; files you edited stay where they are.

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
part["subpart-E"], part["164.512/k/1"]  # addresses, relative to the node
part["164.512"]["k/1"], part["45/164.512/k"]
part.get("164.999")  # None if missing
part.iter("section")  # all sections
part.find(level="appendix")  # first match

paragraph = part["164.512(k)"]
paragraph.citation  # '45 CFR 164.512(k)'
paragraph.heading  # 'Standard: Uses and disclosures for specialized government functions'
paragraph.text  # (k) and everything in it, as Markdown
paragraph.own_text  # only (k)'s lead text, up to (k)(1)
paragraph.parent, paragraph.section, paragraph.ancestors
```

Nodes have `level`, `identifier`, `heading`, `citation`, `address`, `uri`, `cite()`, `url` (the eCFR page), `slug` (its name in a saved tree), `hierarchy`, `date`, `provenance`, `parent`, `children`, `text`, `to_markdown()`, `to_markdown_tree()`, `toc()`, `to_dict()` and `save()`. `text` is everything inside the node on every level, the same as `to_markdown()`.

Level names (`"section"`, `"subject_group"`, …) are case-insensitive; a name that isn't a level, such as `"sections"`, raises `InvalidParameterError` listing the levels. `Level` members are strings, so `node.level == "section"` works.

### Sized for a prompt

```python
part.toc()  # citations and headings, indented, down to paragraphs; no text
part.toc(depth=2)  # two levels below the part
part.to_markdown(max_chars=20_000)  # cut at a paragraph break, with a note
```

### JSON

`to_dict()` returns a dict that `json.dumps` accepts, with the same keys on nodes, outlines, changes and matches: `citation`, `address`, `uri`, `level`, `identifier`, `heading`, `date` (ISO format), `url`, plus `text` and `children` on nodes, `reserved`, `size` and `children` on outlines, `issued`, `effective` and `kind` on changes, and `excerpt`, `starts_on` and `ends_on` on matches. `depth=` limits the children (`0` for none), `text=False` leaves out nodes' text (a node's text includes its children's), and `include_raw=True` adds the API's response as `raw`. Use `to_dict()` rather than `dataclasses.asdict` or pydantic, which don't know about dates and back-references.

In Jupyter, nodes display as Markdown and outlines as a nested list, cut at about 20,000 characters.

If more than one node matches a key, `node[key]` raises `AmbiguousNodeError` with the matches in `.candidates`. `find()` returns the first match.

### Next, previous and up

Nodes from `get` know where they are in the CFR. `next` and `previous` are the neighbouring sections or appendices, in document order, across subparts, parts, chapters and titles (reserved sections are skipped); `up` is the parent, or above the text that was fetched, the division that holds it. Reading a section in a part under 2 MB fetches the whole part once (and caches it), so stepping through its sections makes no more requests; elsewhere, the next text is fetched.

```python
section = ecfr_md.get("45 CFR 164.502")
section.next  # <Section 45 CFR 164.504 ...>, from the part already fetched
section.previous.previous  # 45 CFR 164.500
section.up  # <Subpart 45 CFR Part 164 Subpart E ...>
section.outline  # its entry in the outline of the whole CFR
section.outline.next.read()  # the text of the next section
```

Nodes built with `ecfr_md.load(xml, title=45)` have no client to fetch with, so these raise `ECFRError`.

Outlines move the same way, without text. `ecfr_md.outline()` with no citation is the whole CFR: one entry per title, each title's table of contents loaded the first time it's opened, and kept for the session.

```python
cfr = ecfr_md.outline()
cfr["45"]["Part 164"]  # opens title 45
entry = ecfr_md.outline("45 CFR Part 164")
entry.next  # the first section after part 164: 45 CFR 170.100
entry.up  # 45 CFR Subchapter C, above this outline
entry.siblings  # the other parts in the subchapter
entry["164.502"].read()  # the text
entry.url  # the eCFR page
```

`parent` stays inside the outline you asked for (None at its top); `up`, `siblings`, `next` and `previous` look beyond it.

### Cross-references

`node.references` lists the cross-references in a node's text, each with where it is (`start`, `end`, `text`), what it points to (`target`, an `Address`, and `citation`) and a link (`url`):

```python
for ref in ecfr_md.get("45 CFR 164.502").references:
    print(ref.text, "->", ref.citation, ref.url)
# this subpart -> 45 CFR Part 164 Subpart E https://www.ecfr.gov/current/title-45/part-164/subpart-E
# subpart C of part 160 of this subchapter -> 45 CFR Part 160 Subpart C https://...
# § 164.506 -> 45 CFR 164.506 https://www.ecfr.gov/current/title-45/section-164.506
# ...
# 78 FR 5696 -> 78 FR 5696 https://www.federalregister.gov/citation/78-FR-5696
```

`ecfr_md.find_references(text, context)` does the same for any text; `context` is where the text is (an address or citation such as `"45/164.502"`), which resolves "this part", "subpart E of this part" and "paragraph (b) of this section". It understands `§ 164.501`, `§§ 164.502 and 164.504`, `§§ 164.500 through 164.534`, `§ 164.502(a)(1) and (2)`, `45 CFR 160.103`, `45 CFR parts 160 and 164`, `part 160 of this subchapter`, `part 2 of title 42`, `subpart E of this part`, `paragraphs (a)(1) and (2) of this section`, `paragraph (c) of § 164.508`, `appendix A to this part`, `Appendix A to Subpart C of Part 164`, `this part`, `this subpart` and `this section`, plus `42 U.S.C. 1320d-2`, `78 FR 5692` and `Pub. L. 104-191`. The link templates for those three are `USC_URL`, `FR_URL` and `PUBLIC_LAW_URL` in `ecfr_md.references`; `urls={"usc": ...}` overrides them.

Resolution is by pattern. Measured in November 2026 on six parts (45 CFR 160 and 164, 42 CFR 482, 12 CFR 1026, 21 CFR 314, 34 CFR 300; 589 sections and appendices, 15,706 CFR references):

- 99.9% of the sections, parts, subparts and appendices referred to exist; 96.5% of the paragraphs referred to in the same part are found in the text, most of the rest in sections whose paragraphs ecfr-md nests differently (`paragraph_issues`).
- 99.6% of citation-like phrases in sections are found. Those missed are mostly "paragraph (2) of this definition" and "50 U.S.C. app. 527", which aren't resolved. In appendices it's 96.1%: commentary such as 12 CFR Supplement I to Part 1026 says "this section" and "paragraph (d)(2)" of the section it comments on, which the text doesn't mark.

`ecfr_md.cited_by("45 CFR 164.502")` finds sections that may cite a section or part by searching the eCFR for its number. It's approximate: it finds "§ 164.502" and "45 CFR 164.502", not "paragraph (a) of this section" or text that cites the part as "this part".

### Anchors

`node.anchor` is the id the eCFR gives the node on its pages, for deep links and HTML anchors: `p-164.502(a)(1)` for a paragraph, `164.502` for a section, `subpart-E`, `part-164`, `Appendix-A-to-Part-164`. The eCFR includes the defined term in the ids of paragraphs numbered under definitions (`p-164.501(Health care operations)(1)`), which the text doesn't mark, so those differ.

### Outlines, changes and search results

`outline()` returns the table of contents as an `Outline`, without the text. It navigates like a node (`outline["Subpart E"]`, `outline["subpart-E"]`, `outline.iter("section")`, `outline.find(level="appendix")`, `.parent`, `.ancestors`, `.children`) and has `level`, `identifier`, `heading`, `citation`, `address`, `uri`, `url`, `reserved` and `size`; `str(outline)` is the indented table of contents. `level` is a `Level`, or the eCFR's own type name (such as `"hed1"`) for unnumbered headings.

`changes()` returns `Change` objects, newest first: `citation`, `level`, `heading`, `url`, `issued` (when the eCFR published it), `effective` and `kind` (`"substantive"`, `"editorial"` or `"removed"`).

`search()` returns `Match` objects, best first: `citation` (pass it to `get`), `level`, `identifier`, `heading`, `url`, `excerpt`, `starts_on` and `ends_on`. The list's `.total` is how many the eCFR found. Words are matched as a phrase unless `phrase=False` or the query has its own quotes; `limit`, `all_versions` and the API's filters (`agency_slugs`, `date`, `last_modified_after`, …) are keywords.

Each keeps the API's response in `.raw`.

### Paragraphs

The XML lists a section's paragraphs flat. They are nested by marker: (a), (1), (i), (A), then italic (*1*) and (*i*). Markers that could be a letter or a roman numeral, like (i), are decided by what comes before. Also handled:

- run-in children: `(k) Standard—(1) Military—(i) …`
- reserved ranges: `(b)-(d) [Reserved]`
- worked examples (`Example 1. (i) Facts…`), which restart numbering and are kept with the paragraph they belong to

Some sections don't follow the numbering rules, such as definitions that number items under each term, and the vehicle safety standards in 49 CFR Part 571, which number paragraphs S1, S4.1 and so on (these aren't split into paragraphs). `Section.paragraph_issues` lists repeats and gaps. `Section.to_markdown()` renders the source as-is either way.

### Defined terms and quotations

`define` finds the definition of a term that governs a place: the place's own text, then the definitions sections of each division it's in, nearest first, keeping those that apply there. `verify_quote` checks a quotation word for word (formatting, quote marks and dashes aside) at a pinpoint, and otherwise says where it is or what the nearest text is.

```python
found = ecfr_md.define("covered entities", "45 CFR 164.502")
found[0].cite()        # '45 CFR 160.103 (definition of "Covered entity")'
found[0].applies_to    # '45 CFR Subtitle A Subchapter C'

check = ecfr_md.verify_quote("45 CFR 164.502(b)", "the minimum necessary to accomplish the intended purpose")
check.verified, check.citation
```

```sh
ecfr-md define "covered entity" 45 CFR 164.502
ecfr-md verify "45 CFR 164.502(b)" --quote "minimum necessary"   # exit 1 unless verified
```

`ecfr_md.passages` cuts text to a token budget at paragraph boundaries, with a cursor to continue (`passage(node, 2000)`), and maps any offset in a node's text to its paragraph (`TextIndex`).

## AI agents

`ecfr-md mcp` is an [MCP](https://modelcontextprotocol.io) server that gives agents the CFR the way an expert reads it: outlines with token estimates to plan, text sized to a budget with a cursor, full pinpoint citations, the governing definition of a term, quote checking, redlines between dates, and change notifications. Add it to Claude Code with:

```sh
claude mcp add ecfr -- uvx --from "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md" ecfr-md mcp
```

Other clients take the same command in their JSON config (`"command": "uvx"`, `"args": ["--from", "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md", "ecfr-md", "mcp"]`). `--http` serves streamable HTTP instead of stdio. The tools are read-only; `export` writes only under `--export-dir` (default: the current directory; `--no-export` removes it), and agents can change settings only with `--allow-config-changes`. Python apps can serve it themselves: `ecfr_md.mcp.server(ecfr)` returns the FastMCP server.

Agents with a shell can use the command line instead: [`skills/ecfr-md/SKILL.md`](skills/ecfr-md/SKILL.md) is a skill that teaches it, and every command takes `--json`. The [AI agents guide](https://ptmorris05.github.io/ecfr-md/agents/) lists the tools, resources and prompts, and the measured cost of common tasks.

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
- **A section brings its part.** Reading a section or appendix in a part under 2 MB fetches the part, so `next`, `previous` and other sections of the part need no request. The table of contents is fetched once to tell (and cached).
- **Offline.** If the eCFR can't be reached, ecfr-md uses the newest cached copy of the title list and the text, with an `ECFRWarning` that it may be out of date.
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

Everything exported from `ecfr_md` (the module-level functions, `ECFR`, `Settings`, `Citation`, the node classes, `Level`, `LevelName`, `Format`, `Outline`, `Change`, `Match`, `Matches`, `Reference`, `find_references`, `load`, `Progress`, `SyncResult`, the search enums and typed dicts, and the exceptions), plus the response models in `ecfr_md.models`. Other modules are internal. The `ecfr-md` command and its options are public too.

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
| `MissingDependencyError` | Saving Word or PDF without its extra installed; the message says what to install. Also an `ImportError`. |

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

The documentation site is built from this README, the changelog and the docstrings: `uv run --group docs mkdocs serve`. To release, set the version in `pyproject.toml`, move the changelog's Unreleased notes under that version, and push a tag `vX.Y.Z`; the release workflow checks, tests, builds and publishes it. See [CONTRIBUTING.md](https://github.com/ptmorris05/ecfr-md/blob/main/CONTRIBUTING.md).

## License

[MIT](https://github.com/ptmorris05/ecfr-md/blob/main/LICENSE). The CFR text itself is a U.S. government work and not under copyright; the eCFR is an editorial compilation, not an official legal edition.
