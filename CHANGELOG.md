# Changelog

Versions follow [Semantic Versioning](https://semver.org/). The public API is
everything exported from `ecfr_md`, the response models in `ecfr_md.models`,
and the `ecfr-md` command. Before 1.0, minor versions may change it.

## [Unreleased]

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
