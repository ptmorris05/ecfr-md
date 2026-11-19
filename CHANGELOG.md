# Changelog

Versions follow [Semantic Versioning](https://semver.org/). The public API is
everything exported from `ecfr_md`, the response models in `ecfr_md.models`,
and the `ecfr-md` command. Before 1.0, minor versions may change it.

## [Unreleased]

Install from GitHub; ecfr-md is not on PyPI.

### Added

- Module-level functions sharing one default client: `ecfr_md.get`, `save`,
  `get_many`, `outline`, `changes`, `search` and `sync`.
- `ECFR.search(query)`: matching sections as `Match` objects (citation,
  heading, excerpt), current text only and one per section; `.total` is the
  number the eCFR found. Words are matched as a phrase unless `phrase=False`.
- `ecfr-md search --json` and `--no-phrase`; `--agency SLUG` replaces
  `--agency-slugs`.
- A missing paragraph's error lists the paragraphs that exist there.
- `sync(dry_run=True)` (`ecfr-md sync --dry-run`) reports what would change
  without writing anything.
- Saved files record a `digest` of their text in the front matter. `sync`
  keeps files edited since they were saved and lists them in
  `SyncResult.modified` ("modified locally"); `force=True` (`--force`)
  overwrites or deletes them. Files without a digest sync as before.
- Text saved as of a past date records `pinned: "<date>"` in the front matter.
  `sync` keeps it at that date and lists it in `SyncResult.kept`;
  `include_dated=True` (`--include-dated`) updates it.

### Changed

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
  `45-cfr-part-164@2018-01-01.md`, so snapshots don't overwrite each other.
- Saving a tree over an earlier save of the same tree removes files ecfr-md
  wrote for sections the new save doesn't have, so a tree never mixes dates.
- Telling a past date from the latest uses the title list (kept for a few
  minutes), so a dated request without the cache makes one more small request.

### Fixed

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
