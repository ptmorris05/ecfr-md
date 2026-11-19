# Changelog

Versions follow [Semantic Versioning](https://semver.org/). The public API is
everything exported from `ecfr_md`, the response models in `ecfr_md.models`,
and the `ecfr-md` command. Before 1.0, minor versions may change it.

## [Unreleased]

## [0.2.0] - 2026-11-19

On PyPI: `uv tool install ecfr-md` or `pip install ecfr-md`, with the extras `web`, `tui`, `mcp`, `docx`, `pdf` and `all`.

### Added

- `ecfr-md browse [CITATION] [--date DATE]`: a full-screen terminal browser
  (the `tui` extra, Textual). The outline of the whole CFR beside the text,
  with a breadcrumb and the date of the text. Everything works with the
  arrow keys, Enter and Esc, shown in the footer: in the text, `↑`/`↓`
  paragraphs, `←`/`→` cross-references, Enter follows one or opens an
  entry of contents, Esc goes back (from the first place, up a level; it
  never quits); in the outline, `↑`/`↓` move, `→`/`←` open and close
  branches or go in and out, Enter reads; `tab` moves between the two; in
  dialogs, `↓`/`↑` go from the box to the list and back, Enter chooses,
  Esc closes. Letter keys are shortcuts: `g` go to any citation
  (completed from the outline), `/` search, `[`/`]` previous and next
  section, `j`/`k` paragraphs, `u` up, `b`/`f` back and forward, `d` text
  as of a date, `h` changes with a redline of each, `e` export in every
  format, `y` copy the paragraph's citation, `o` the outline, `?` keys.
  The mouse works too. The `all` extra includes `tui`.
- An MCP server for AI agents: `ecfr-md mcp` (stdio, or streamable HTTP
  with `--http`), and `ecfr_md.mcp.server(ecfr)` for apps that serve it
  themselves. Needs the new `mcp` extra (FastMCP 4.2), which `all` now
  includes. Read-only tools with output schemas and Markdown results:
  `list_titles`, `outline` (token estimates per entry), `read` (the text
  that fits a token budget, with a cursor to continue, full pinpoint
  paragraph labels, paragraph ids and what's around it), `search`,
  `changes`, `history`, `compare` (located redline with the amendments
  between the dates), `references`, `cited_by`, `resolve_citation`,
  `verify_quote`, `define` and `settings`; `export` writes only under
  `--export-dir`, and `configure` exists only with
  `--allow-config-changes`. Resources `cfr:{address}`, `cfr:titles` and
  `ecfr-md:settings`, with subscriptions that notify when the eCFR changes
  the text; citation completions; prompts for summarizing obligations,
  what changed, comparing provisions and finding a term; progress and
  cancellation for large fetches.
- `ecfr_md.define()` / `ECFR.define(term, citation)` and
  `ecfr-md define TERM CITATION`: the definition of a term that governs a
  place in the CFR, found in the place's text and the definitions sections
  that apply to it (`Definition`, `Definitions`).
- `ecfr_md.verify_quote()` / `ECFR.verify_quote(citation, text)` and
  `ecfr-md verify CITATION --quote TEXT`: whether a quotation is word for
  word at a pinpoint, else where it is or the nearest text (`QuoteCheck`).
- `ecfr_md.passages`: text cut to a token budget at paragraph boundaries
  with a cursor (`passage`, `Passage`), and the paragraph at any offset
  (`TextIndex`).
- `--json` on `get` (the same as `--format json`), `sync` and
  `cache clear`, so every command can print JSON.
- An agent skill, `skills/ecfr-md/SKILL.md`, that teaches the command
  line, and an AI agents guide with setup for Claude Code and other MCP
  clients.
- Not-found errors suggest near misses: "did you mean 164.502?".
- `ecfr-md serve`: a web navigator for the CFR, run on this computer
  (`web` extra: Starlette, uvicorn, Jinja2). Paths mirror ecfr.gov
  (`/current/...`, `/on/DATE/...`, `/compare/LATER/to/EARLIER/...`), and
  `/go?cite=` resolves any citation. Pages for titles, divisions (contents
  with section counts and sizes), text, history, comparisons (an inline
  word-level redline), search, export in every format (downloaded, or saved
  to `--save-dir`) and settings (every `Settings` field, applied at once and
  optionally written to the config file). A command palette (`⌘K`) for
  citations, headings, known names, words, dates and actions; hover
  previews of cross-references and "open beside"; defined terms underlined
  with their definitions; paragraph and range deep links; copy with
  citation (OFR or Bluebook); a versions timeline; outline, focus, text size,
  density, light and dark; `j`/`k`/`[`/`]`/`u` keys. Text streams with a
  progress hairline, neighbouring pages are prerendered, a service worker
  keeps read pages offline, and there is a print stylesheet. Every page is
  also `.md` and `.json`, with `/llms.txt` and `/openapi.json`. It answers
  only local host names unless `--host` opens it, and refuses cross-site
  requests that change anything. `ecfr_md.web.create_app()` is the ASGI app.
- `ECFR.definitions(citation)`: every defined term that governs a place,
  nearest first, as the web navigator underlines them.
- `ecfr_md.compare.aligned()`: both texts block by block, unchanged blocks
  included, for a whole-text redline; `Difference.kind` is `"unchanged"`
  for those.
- The HTML writer is a class, `render.html_writer.Writer`, with a method
  per kind of block to override; inline `<ins>` and `<del>` are kept as
  insertions and deletions.
- The `all` extra includes `web`.
- MIT license. Releases are published on GitHub from version tags, with the
  wheel and source distribution attached and build provenance attested.
- A documentation site with an API reference, built from the README,
  changelog and docstrings.
- Navigation: nodes from a client have `next` and `previous` (the
  neighbouring section or appendix, across parts, chapters and titles),
  `up` (the parent, or the division above the text fetched) and `outline`
  (their entry in the outline of the whole CFR). Outline entries have
  `next`, `previous`, `up`, `siblings` and `read(date=)`, which fetches the
  text. Nodes from `load()` raise `ECFRError` for these.
- `ecfr_md.outline()` / `ECFR.outline()` with no citation: the whole CFR,
  one entry per title, each title's structure loaded when first opened.
- Cross-references: `Node.references`, `find_references(text, context)`
  and `Reference` (`start`, `end`, `text`, `target`, `citation`, `url`,
  `kind`). Sections, lists and ranges of them, paragraphs "of this section"
  or "of § X", parts, subparts and appendices relative to the text, other
  titles, and citations of the U.S. Code, the Federal Register and public
  laws, which link to uscode.house.gov, federalregister.gov and
  congress.gov (templates `USC_URL`, `FR_URL`, `PUBLIC_LAW_URL`).
- `ecfr_md.cited_by()` / `ECFR.cited_by()` and `ecfr-md cited-by`: sections
  that may cite a section or part, found by search (approximate).
- `ecfr-md references CITATION`: a section's cross-references and what
  they refer to.
- Linked Markdown: `links=` on `save()`, `Node.save()` and `--links` /
  `--no-links` on `get`; `to_markdown(links=True)`. Trees link their
  cross-references to each other's files by default, and to the eCFR for
  anything outside the tree. Front matter records `links`.
- `Node.anchor`: the eCFR's id for a paragraph (`p-164.502(a)(1)`),
  section (`164.502`), subpart, part or appendix.
- `ecfr-md outline` of a section or appendix ends with the previous and
  next ones.
- Offline reading: when the eCFR can't be reached, the newest cached title
  list and text are used, with an `ECFRWarning` that they may be out of
  date.
- `ecfr-md titles`, `ecfr_md.titles()` and `ECFR.titles()`: every title with
  the latest date of its text (`TitleInfo`). A bare `ecfr-md outline` lists
  them too.
- `ecfr-md compare CITATION --since DATE [--until DATE]`, `ECFR.compare()`
  and `ecfr_md.compare()`: what changed between two dates, paragraph by
  paragraph and word by word (`Comparison`, `Difference`). `changes` ends
  with the compare command for the latest change.
- `search --in CITATION` (`within=` in Python, and on every search
  endpoint) limits a search to a title or any part of it. Unquoted words
  form one query.
- `get_many(..., errors="return")` keeps going past citations that fail.
- In a terminal, `get` indents paragraphs, shows the section and parent
  paragraphs above a paragraph, pages long text (`--no-pager`), shows
  footnote marks as superscripts, and ends with the citation, date and
  eCFR link.
- `get -o -` prints to stdout; `get --from -` reads stdin; `--from` lines
  may have `# comments`, quotes and lists.
- `cache info --json`.
- Export formats besides Markdown: plain text, HTML, JSON, EPUB, Word and PDF.
  `Node.export(path, format=None)` and `save(..., format=)` on nodes, `ECFR`
  and `ecfr_md` pick the format from the file's suffix (`hipaa.pdf`) unless
  `format=` names one; `ecfr-md get -o hipaa.pdf` and `--format` likewise,
  and `get --format json|text|html` prints. HTML pages are standalone, light
  and dark, print well, and anchor paragraphs with the eCFR's ids
  (`#p-164.502(a)(1)`). JSON is the node tree with each node's own text and
  provenance. Word documents use Word's heading styles; PDFs have a bookmark
  per heading, a running head and page numbers. With `tree=True` (`--tree`,
  and `--all`), text and HTML get a file per section and an index page per
  level, and the others a file per part, written as the text is read.
- Extras `docx` (python-docx), `pdf` (Typst, which needs no system libraries)
  and `all`. Without one, saving raises `MissingDependencyError`, which says
  what to install.
- Addresses: one short, shell-safe name for every place in the CFR, e.g.
  `45/164.502/a/1`, `45/part-164/subpart-E`, `12/part-1026/supplement-I`,
  `26/1.401.k-1` (26 CFR 1.401(k)-1), `45/164.501/1~2` (the second of two
  (1)s), `45/164.502@2020-01-01`; lists (`45/164.502,164.504`) and ranges of
  paragraphs (`45/164.502/b..d`).
- `Address`: `Address.parse` reads addresses, citations (now also Bluebook
  forms, spaced `164.502 a 1` and dotted `164.502.a.1` markers), eCFR, LII and
  GovInfo links, GovInfo granule ids, USLM identifiers, `cfr:` URIs and the
  names of saved files, old and new. `str()`, `.citation()` (`"ofr"` or
  `"bluebook"`), `.url()`, `.uri()`, `.file_name()`; `Address.parse_many` for
  lists.
- Everything that takes a citation takes any of those: `get`, `save`,
  `outline`, `changes`, `get_many`, `search(within=)`, `get --from` and the
  CLI's citation words.
- A trailing year, as in a Bluebook citation (`45 C.F.R. § 164.502 (2018)`),
  means that year's annual edition of the title (January 1 for titles 1–16,
  April 1 for 17–27, July 1 for 28–41, October 1 for 42–50), instead of being
  an error. A date in an address (`@2020-01-01`) is used likewise.
- `Node.address`, `Node.uri` (`cfr:45/164.502@2024-01-01`), `Node.cite(style=,
  year=)`; `address` and `uri` on `Outline`, `Change`, `Match` and `TitleInfo`
  and in every `to_dict()`. `Paragraph.markers` (as `path`) and
  `Paragraph.occurrence`.
- Node and outline lookups take addresses, relative or not: `part["subpart-E"]`,
  `part["164.512/k/1"]`, `section["a/1"]`, `section["1~2"]`, `node[address]`.
- `ecfr-md get 45/164.502 45/164.504 -o DIR/` saves several, one file each
  (printed one after another without `-o`). `ecfr-md get` with no citation, in
  a terminal, asks for one ("Citation or address:").
- Saved files' front matter records `address` and `layout: 2`.
- `sync(migrate=True)` (`ecfr-md sync --migrate`) renames files and trees
  saved by ecfr-md 0.x to today's layout; `SyncResult.moved` lists them.

- `ecfr-md get --all -o DIR`: every title, one file each, or with `--tree` one
  folder per title (`DIR/title-N/`). Reserved titles are skipped; `sync DIR`
  updates them all.
- Module-level functions sharing one default client: `ecfr_md.get`, `save`,
  `get_many`, `outline`, `changes`, `search` and `sync`.
- `ECFR.search(query)`: matching sections as `Match` objects (citation,
  heading, excerpt), current text only and one per section; `.total` is the
  number the eCFR found. Words are matched as a phrase unless `phrase=False`.
- `ecfr-md search --json` and `--no-phrase`; `--agency SLUG` replaces
  `--agency-slugs`.
- A missing paragraph's error lists the paragraphs that exist there.
- Citations are accepted as people paste them: surrounding quotes and
  invisible characters are ignored, `Supp. I` and `app. J` are completed from
  the part (`12 CFR pt. 1026, app. J`), `et seq.` is ignored, and
  `Subpart E of Part 164` and `Part 164 of Title 45` work. `CRF` for `CFR`,
  unbalanced parentheses, and ranges of sections are rejected with what to do
  instead.
  `Citation.parse_many` splits a list such as `45 CFR §§ 164.502, 164.506`.
- `to_dict(depth=, include_raw=)` on nodes (also `text=`), `Outline`, `Change`
  and `Match`: JSON-ready dicts with the same keys (`citation`, `level`,
  `identifier`, `heading`, `date`, `url`, ...). The API's response (`raw`) is
  left out unless asked for.
- `url` (the eCFR page) on nodes, `Outline`, `Change` and `Match`; `level` and
  `identifier` on `Match`, `level` on `Change`; `date` on `Outline`.
- `Node.toc(depth=)`: citations and headings down to paragraphs, without text.
- `Node.to_markdown(max_chars=)` cuts at a paragraph break and says how many
  characters were left out.
- `Paragraph.own_text`: a paragraph's lead text without its subparagraphs.
- Nodes and outlines display as Markdown in Jupyter, capped at about 20,000
  characters.
- `Outline.find()` and `Outline.ancestors`, as on nodes.
- `LevelName`, the level names as a `Literal` type.
- The module-level functions have their `ECFR` methods' full docstrings, and
  signatures show `DateLike` instead of its validators. Node classes and
  `Level` have docstrings.
- `sync(dry_run=True)` (`ecfr-md sync --dry-run`) reports what would change
  without writing anything.
- Saved files record a `digest` of their text in the front matter. `sync`
  keeps files edited since they were saved and lists them in
  `SyncResult.modified` ("modified locally"); `force=True` (`--force`)
  overwrites or deletes them. Files without a digest sync as before.
- Text saved as of a past date records `pinned: "<date>"` in the front matter.
  `sync` keeps it at that date and lists it in `SyncResult.kept`;
  `include_dated=True` (`--include-dated`) updates it.
- GovInfo's bulk eCFR XML as a second source of text. `source="auto"`
  (the default; also `"api"` or `"bulk"`) picks one per request: past dates
  and parts or less from the API, a title already in the cache as a bulk
  copy from that copy, and larger text from whichever is estimated faster,
  from request latency and download speed learned and kept in the cache
  (`api_latency`, `bulk_bandwidth`, `learn_costs`, `bulk_min_bytes`). Bulk
  is used only when GovInfo's file is as current as the eCFR, and any
  failure falls back to the API. Bulk files are rewritten in the API's
  dialect; on seven titles, 33,830 of 33,850 sections render identically.
  `-v` logs which host served each text and why. A nightly test compares
  the two sources.
- Every host is a setting: `site_url` (page links), `image_url`, `bulk_url`,
  and link templates for other codes (`usc_url`, `fr_url`,
  `public_law_url`), besides `base_url`. Cache keys include the host, so a
  mirror's text never mixes with the official text.
- A config file (TOML; `--config PATH` or `ECFR_MD_CONFIG`, else
  `config.toml` in the user config directory) and `ECFR_MD_*` variables for
  every setting. `Settings.load()` reads them as the command line does:
  arguments, then options, then the environment, then the file.
  `Settings.explain()` gives each value and where it came from.
- `ecfr-md config show | path | set KEY VALUE | unset KEY | edit`.
- `ecfr_md.configure(...)` sets the module-level functions' settings.

### Changed

- **Search finds words in any form.** Plain words now find the exact
  phrase first and then sections with all the words, stemmed, so
  "ethnic slur" finds "ethnic slurs" (it found nothing before, because
  words were always searched as an exact phrase, which the eCFR doesn't
  stem). `phrase=True` / `--phrase` keeps only the exact phrase;
  `phrase=False` / `--no-phrase` only the words. A quoted phrase that
  matches nothing is searched again as words, with a note. In the SDK,
  CLI, web navigator, terminal browser and MCP server alike.
- **Search operators**: `"exact phrase"`, `-exclude`, `a | b`, `( )`,
  `prefix*` and `"phrase"~N` go to the eCFR as written, and `AND`, `OR`,
  `NOT` in capitals are read as operators; unbalanced quotes and
  parentheses are explained (`ecfr_md.query`). `Matches` has `query`,
  `exact` and `note`; results mark the query's words in any form.
- `ECFR()` without settings reads the config file and `ECFR_MD_*`
  variables (`Settings.load()`); `Settings()` is still the defaults.
  `Settings` rejects unknown names.
- Divisions above the part from `get` read each part from disk when it's
  needed: `get(40)` peaks at about 130 MB instead of 1.3 GB. Text is parsed
  from its file rather than from bytes in memory.
- Outlines are kept as lean entries (Title 40: 12 MB, was 56 MB) in bounded
  caches; `Outline.raw` is built when asked for.
- Large titles are fetched largest part first and joined file to file,
  without holding parts in memory.
- `get_many` fetches what it can't group by part in parallel.
- `sync` asks whether anything was issued since a file's date before saving
  it again; unchanged files cost at most one small request per title.
- The link templates `USC_URL`, `FR_URL` and `PUBLIC_LAW_URL` are defined in
  `ecfr_md.config` (still importable from `ecfr_md.references`).
- **Breaking:** saving to a `.txt` file writes plain text, not Markdown, and
  other suffixes (`.html`, `.pdf`, ...) save that format; `get -o` no longer
  rejects them. A suffix that names no format is an error; names without a
  suffix are still Markdown.
- Reading a section or appendix in a part under 2 MB fetches the whole
  part (once, then from the cache), so the part's other sections need no
  request; the node's `parent` is now its subpart or part. The title's
  structure is fetched first to tell (also cached).
- **One-time rewrite:** trees are now saved with links, so the first `sync`
  of a tree saved before this rewrites all its files to add them. Trees
  saved with `links=False` (`--no-links`) stay without.
- **Breaking:** `--concurrency` defaults to 2 (was 4) and bounds requests in
  flight across all threads: the eCFR answers 429 to 3 or more. After a 429
  the limit halves and never returns to the level that got it; 429s don't
  use up `--max-attempts`; the throttle settles below the rate that got a
  429 instead of climbing back.
- **Breaking:** `search --json` is an object, `{"complete",
  "total_versions", "results"}`; `Matches.complete` says whether every match
  is listed. The summary counts sections, not versions.
- **Breaking:** `outline --json` prints `to_dict()` (citation, level,
  heading, url, children) and honours `--depth`, instead of the API's JSON.
- **Breaking:** a date after a title's latest gives the latest text with a
  warning, labeled with that date (it raised `DateOutOfRangeError`).
- Not-found errors are `NodeNotFoundError` (a `LookupError`) naming what is
  missing and what exists, instead of an HTTP 404 with the API's URL.
- Unknown keyword arguments are errors with a suggestion; ints are
  accepted for levels; argument and setting errors are in plain words.
- When stderr isn't a terminal, messages and summaries are whole lines.
- Narrow terminals get lists instead of squeezed tables.
- **Breaking:** saved files are named by address. A single file is the
  address with `_` for `/`: `45_164.502.md` (was `45-cfr-164.502.md`),
  `45_part-164_subpart-E.md`, `45_164.502_a_1@2020-01-01.md` for text as of a
  past date. Trees use the eCFR's case and addresses' names:
  `part-164/subpart-E/164.502.md` (was `part-164/subpart-e/section-164.502.md`),
  `part-1026/supplement-I.md` (was
  `part-1026/appendix-supplement-i-to-part-1026.md`). Subject groups no longer
  get a folder (their sections go in the subpart's, under the group's heading
  in `index.md`); an appendix to a subpart goes in that subpart's folder; names
  that differ only in case get `~2` in code-point order (`subpart-CC`,
  `subpart-Cc~2`), not `-2` in document order. `sync` keeps updating files
  and trees saved before under their old names; `--migrate` renames them.
- **Breaking:** `Node.slug` is the node's name in a saved tree (`"164.502"`,
  `"subpart-E"`, `"supplement-I"`; a paragraph's marker, `"a"`), and
  `to_markdown_tree()` uses the new names.
- **Breaking:** where a paragraph marker repeats among its siblings, as in
  definitions, `ecfr.get` of the bare marker (`45 CFR 164.108(1)`) raises
  `AmbiguousNodeError` naming each by address (`45/164.108/1~1`, `.../1~2`).
- `Citation.parse` still rejects a trailing year, which a `Citation` can't
  hold, and says that `Address.parse` reads it.

- **Breaking:** `Paragraph.text` is the paragraph and its subparagraphs, like
  `text` on every other node and `to_markdown()`. Its own lead text is now
  `Paragraph.own_text`.
- An unknown level name, as in `node.iter("sections")` or
  `outline.iter("sectoin")`, raises `InvalidParameterError` listing the levels
  (a `ValueError`, as before for nodes); outlines used to yield nothing. Level
  names are case-insensitive. `iter()` checks the level when called, not when
  iteration starts.
- `Outline.level` is a `Level`, or the eCFR's type name for unnumbered headings
  (`"hed1"`). `Outline.parent` is a property rather than a dataclass field, so
  `dataclasses.asdict(outline)` no longer recurses forever; `Outline(...)` no
  longer takes `parent`.
- `ECFR.outline` returns an `Outline` that navigates like a node and prints as
  a table of contents, instead of the API's model (now `.raw`).
- `ECFR.changes` returns `Change` objects (`citation`, `heading`, `issued`,
  `effective`, `kind`) instead of the API's models (now `.raw`).
- `save` returns the path written; with `tree=True`, the list of files.
- The endpoint services moved to `ECFR.api.admin`, `ECFR.api.search` and
  `ECFR.api.versioner`, and their commands under `ecfr-md api`.
- `ECFR.download`, `request`, `request_model` and `store` are private.
- `ecfr-md search` is a plain command; it matches phrases by default.
- Command help lists only the command's options; `ecfr-md --help` lists the
  connection options. The top-level help shows common commands.
- Errors are titled in words ("Not a citation", "Not found") instead of
  exception names, and suggest a fix where there is one: a title number for
  `164.502`, `ecfr-md search` for words that aren't a citation.
- Saving one file, syncing with no changes and clearing the cache print one
  line instead of a panel.
- `outline --depth` counts what's below the last level shown ("12 sections");
  `changes` shows a single section's heading once; search excerpts end at a
  whole word.
- Default file names for text as of a past date include the date, e.g.
  `45_part-164@2018-01-01.md`, so snapshots don't overwrite each other.
- Saving a tree over an earlier save of the same tree removes files ecfr-md
  wrote for sections the new save doesn't have, so a tree never mixes dates.
- Telling a past date from the latest uses the title list (kept for a few
  minutes), so a dated request without the cache makes one more small request.

### Fixed

- Redlines in the terminal (`ecfr-md compare`) showed Markdown's `*`
  emphasis marks and `\` escapes; they show the words as they read.
- `45 CFR Subpart E` (a subpart in many parts) silently gave Part 5's;
  it now lists the candidates.
- A subtitle, chapter or subchapter fetched and saved the whole title under
  the narrower citation (the API ignores those filters); it is now cut out
  of the title's text.
- `-o newdir/` and `search --save newdir` wrote a single file.
- Bare `ecfr-md` and command groups (`ecfr-md api`) crashed.
- `get ... | head` reported a broken pipe as an error.
- `-v` logged every HTTP/2 header.
- Using a closed client raised a bare `RuntimeError`.

- `sync` on a tree's top folder rewrote its `index.md` with the whole text.
- `sync` on a directory missed saves more than one folder down.
- `sync` moved snapshots saved with a past date to the latest text, and
  silently reverted local edits.

## [0.1.0]

First release.

- `ECFR.get` and `ecfr-md get`: titles, parts, subparts, sections, appendices
  and paragraphs by citation, at any date.
- Node classes for each CFR level. Paragraphs nested up to six levels; worked
  examples kept out of the numbering; `Section.paragraph_issues`.
- `AmbiguousNodeError` when a lookup matches more than one node.
- Markdown output keeps all source text and escapes text Markdown would
  misread.
- YAML front matter in saved files: citation, date, URLs, retrieval time.
- `ECFR.save` processes XML as it is read; text over 50 MB above the part level
  is fetched one part at a time.
- Cache on by default, reused until the eCFR issues a change; at most one
  request per title to check. Large titles re-download only changed parts.
- `ECFR.get_many` and `ecfr-md get --from`: sections in the same small part
  share one request. `ecfr-md cache info` / `cache clear`.
- HTTP/2, concurrent part downloads, and a rate limit that halves on 429.
- Progress, retries and a summary in the terminal; `-q` / `--quiet`.
- `ecfr-md outline` / `ECFR.outline`: table of contents without the text.
- `ecfr-md changes` / `ECFR.changes`: changes issued, from the versions
  endpoint.
- `ecfr-md search QUERY`, with `--save DIR` to write the current text of
  matches.
- `ecfr-md sync` / `ECFR.sync`: update saved Markdown, rewriting only files
  whose text changed.
- `DateOutOfRangeError` for dates with no text; `ECFRWarning` while a title is
  being updated.
- Typed methods for every eCFR API endpoint, with retries and rate limiting.
