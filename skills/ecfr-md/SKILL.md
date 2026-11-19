---
name: ecfr-md
description: Read, cite and check U.S. federal regulations (the Code of Federal Regulations, CFR) with the ecfr-md command line. Use when a task involves a CFR citation ("45 CFR 164.502", "21 C.F.R. § 314.50"), what a regulation says or requires, a defined term in a regulation, whether a quotation of a regulation is accurate, or how a regulation changed over time.
---

# ecfr-md: the CFR from the command line

`ecfr-md` fetches the eCFR (the current text and any past date) and prints
Markdown, or JSON with `--json` on every command. It caches what it
downloads, so repeating a command is free.

If `ecfr-md` isn't installed, run it with uvx:
`uvx --from git+https://github.com/ptmorris05/ecfr-md ecfr-md ...`

## How the CFR is organized

title > (subtitle) > chapter > (subchapter) > part > (subpart) > section or
appendix > paragraphs (a) > (1) > (i) > (A) > (*1*) > (*i*).
Sections are numbered `part.section`: 45 CFR 164.502 is in part 164 of
title 45. Any common citation form works as an argument: `45 CFR 164.502(a)(1)`,
`45 C.F.R. § 164.502`, `45 CFR Part 164 Subpart E`, eCFR or LII links, or
addresses such as `45/164.502/a/1` and `45/part-164/subpart-E`.

## Work map first, then read

1. **Find the place.**
   - `ecfr-md titles --json` lists the 50 titles.
   - `ecfr-md outline 45 CFR Part 164 --depth 1 --json` is a table of
     contents (`size` is the text's size in bytes; about 4 bytes a token).
   - `ecfr-md search "minimum necessary" --in "45 CFR Part 164" --limit 5 --json`
     finds text.
2. **Read only what you need.** `ecfr-md get "45 CFR 164.502(b)"` prints
   one paragraph; a section or subpart prints whole. For big divisions,
   outline first and read section by section.
3. **Ground what you write.**
   - `ecfr-md define "covered entity" 45 CFR 164.502 --json` finds the
     definition that governs that place (here 45 CFR 160.103, which applies
     to the whole subchapter), with its citation.
   - `ecfr-md verify "45 CFR 164.502(b)" --quote "minimum necessary to accomplish the intended purpose"`
     exits 0 only if the words are at that pinpoint; otherwise it says
     where they are, or the nearest text and how alike it is. Verify every
     quotation before you use it.
4. **Over time and across the CFR.**
   - `ecfr-md changes 45 CFR 164.502 --json`: each change, when issued and
     effective, substantive or editorial.
   - `ecfr-md compare 45 CFR 164.502 --since 2024-01-01 --json`: a
     word-level redline between dates.
   - `ecfr-md get 45 CFR 164.502 --date 2020-01-01`: the text as of a date.
   - `ecfr-md references 45 CFR 164.502 --json`: what it cites, resolved.
   - `ecfr-md cited-by 45 CFR 164.502 --json`: what may cite it (by search).

## Citing

Cite paragraphs as `45 CFR 164.502(a)(1)`, with the date of the text
(`--json` output has `citation`, `date` and the eCFR `url`). Definitions
without paragraph numbers are cited as
`45 CFR 160.103 (definition of "covered entity")`. Say that the eCFR is an
editorial compilation, not an official legal edition, when it matters.

## Errors

Errors say what exists: "No section 164.5021 in 45 CFR Part 164 ... did
you mean 164.502?". Fix the citation from the message rather than guessing.

## Saving

`ecfr-md get 45 CFR Part 164 -o hipaa.md` (or `.txt`, `.html`, `.json`,
`.epub`, `.docx`, `.pdf`); `--tree -o dir/` writes one file per section.

## MCP

`ecfr-md mcp` serves the same features to MCP clients as tools (`outline`,
`read` with a token budget and cursor, `search`, `define`, `verify_quote`,
`compare`, ...). Prefer it when it's configured.
