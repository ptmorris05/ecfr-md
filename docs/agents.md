# AI agents

ecfr-md gives AI agents the same view of the CFR as its other interfaces:
a map before the text, text sized to a budget, precise citations, and
checks against the source. There are three ways in:

- **MCP server**: `ecfr-md mcp`, for any client that speaks the
  [Model Context Protocol](https://modelcontextprotocol.io) (Claude Code,
  Claude Desktop, Cursor, VS Code, ...).
- **Agent skill**: [`skills/ecfr-md/SKILL.md`](https://github.com/ptmorris05/ecfr-md/blob/main/skills/ecfr-md/SKILL.md)
  teaches an agent with a shell to use the command line, where every
  command takes `--json`.
- **Python**: `ECFR.define`, `ECFR.verify_quote` and `ecfr_md.passages`
  are plain SDK code, for agents built in Python.

## Set up the MCP server

The server needs the `mcp` extra. With [uv](https://docs.astral.sh/uv/)
installed, clients can start it with `uvx`, so nothing else needs
installing.

**Claude Code:**

```sh
claude mcp add ecfr -- uvx --from "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md" ecfr-md mcp
```

Settings go before `mcp`, or in `ECFR_MD_*` variables:

```sh
claude mcp add ecfr -e ECFR_MD_CACHE_DIR=/data/ecfr -- \
  uvx --from "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md" \
  ecfr-md --rate-limit 0.5 mcp --export-dir ~/regulations
```

**Other clients** (Claude Desktop's `claude_desktop_config.json`, Cursor's
`.cursor/mcp.json`, VS Code's `.vscode/mcp.json` under `"servers"`, ...):

```json
{
  "mcpServers": {
    "ecfr": {
      "command": "uvx",
      "args": [
        "--from", "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md",
        "ecfr-md", "mcp"
      ]
    }
  }
}
```

If ecfr-md is installed (`uv tool install "ecfr-md[mcp] @ git+https://github.com/ptmorris05/ecfr-md"`),
the command is just `ecfr-md` with the argument `mcp`.

**Over HTTP**, for clients that connect to a URL or for several clients at
once:

```sh
ecfr-md mcp --http --port 8000     # streamable HTTP at http://127.0.0.1:8000/mcp
```

It listens on localhost only unless `--host` says otherwise, and warns
when it does.

### Options

| Option | Effect |
| --- | --- |
| `--http`, `--host`, `--port` | Streamable HTTP instead of stdio |
| `--export-dir DIR` | Where the `export` tool may write (default: the current directory); it refuses paths outside it |
| `--no-export` | No `export` tool: the server writes nothing |
| `--allow-config-changes` | Adds a `configure` tool; otherwise agents can only read the settings |
| `--watch-interval SECONDS` | How often subscribed text is checked for changes (default 3600) |

The global options (`ecfr-md --help`), environment variables and settings
apply as for every command: the host, cache, rate limit and so on.

## What agents get

The server's instructions explain the hierarchy, the citation forms it
accepts and the workflow: **map first, then read**.

### Tools

Every tool is read-only (except `export`) and annotated as such; each
returns readable Markdown, which is what the model reads, and structured
content matching its output schema, for programs.

| Tool | What it does |
| --- | --- |
| `list_titles` | The 50 titles and the latest date of each |
| `outline(citation, depth, date)` | Table of contents with estimated tokens per entry; as many levels as fit unless `depth` is given |
| `read(citation, date, budget_tokens, cursor)` | The text that fits the budget, ending at a paragraph, with a cursor to continue; never cut silently. Paragraph labels are full pinpoints ("(a)(1)(ii)"). Gives the citation, address, date, eCFR link, paragraph ids, parent, previous, next, children and cross-references |
| `search(query, within, limit, date, phrase)` | Full-text search with excerpts |
| `changes(citation, since, until)` | Each change issued, newest first |
| `history(citation)` | The versions over time, grouped by date |
| `compare(citation, from_date, to_date)` | Word-level redline, located by pinpoint, with the amendments between the dates |
| `references(citation)` | What it cites, resolved, and which paragraphs cite each |
| `cited_by(citation)` | What may cite it (approximate, by search) |
| `resolve_citation(text)` | Any citation form to canonical citation, Bluebook, address and URL; checks it exists |
| `verify_quote(citation, text, date)` | Is the quote word for word at that pinpoint? If not, where it is, or the nearest text with a diff |
| `define(term, scope)` | The governing definition: the place's own text, then the definitions sections of each division it's in that apply to it |
| `export(citation, format, path)` | Save in any format under the export directory |
| `settings` | The effective settings |

Errors say what exists and what was probably meant: "No section 164.5021
in 45 CFR Part 164. It has sections 164.102 to 164.535 (41 in all); did you
mean 164.502?".

### Resources, prompts and completions

- `cfr:{address}`: the text of any place as Markdown, e.g.
  `cfr:45/164.502`, `cfr:45/part-164/subpart-E@2020-01-01`. `cfr:titles`
  lists the titles; `ecfr-md:settings` has the settings as JSON.
- **Subscriptions**: subscribe to a `cfr:` resource and the server
  notifies you when the eCFR issues a change to it (MCP
  `subscriptions/listen`, or `resources/subscribe` on earlier protocol
  versions).
- **Prompts**, each a workflow over the tools: `summarize_obligations`,
  `what_changed`, `compare_provisions` and `find_term`.
- **Completions** for citations in resource and prompt arguments: titles,
  then parts (`45/16` → `45/part-160`, `45/part-164`), then sections
  (`45 CFR 164.50` → `45 CFR 164.500`, ...).
- **Progress and cancellation**: large fetches report progress per part,
  and a cancelled call stops its download.

## Measured with agent tasks

`tests/agent_eval/run_eval.py` drives the server through eight realistic
tasks against the live eCFR and checks each answer is grounded. Tokens are
what the model reads (four characters to a token):

| Task | Tool calls | Tokens read |
| --- | --- | --- |
| Find the minimum necessary standard and quote it (search, read, verify_quote) | 3 | 572 |
| Navigate from title 45 to 45 CFR 164.512(f) and read it (outline ×3, read) | 4 | 2,669 |
| What the latest amendment to 45 CFR 164.502 changed (history, compare) | 2 | 1,413 |
| Define "business associate" for 45 CFR 164.504 | 1 | 846 |
| Define "billing cycle" for 12 CFR 1026.7 | 1 | 111 |
| Catch a misquotation of 45 CFR 164.502(a) | 1 | 62 |
| Resolve a Bluebook citation and follow its references | 2 | 169 |
| Recover from a typo ("164.5122") | 1 | 32 |

All eight pass. The Markdown is 2 to 2.5 times smaller than the same
results as JSON.

## The command line, for agents with a shell

```sh
ecfr-md outline 45 CFR Part 164 --depth 1 --json
ecfr-md search "minimum necessary" --in "45 CFR Part 164" --json
ecfr-md get "45 CFR 164.502(b)"
ecfr-md define "covered entity" 45 CFR 164.502 --json
ecfr-md verify "45 CFR 164.502(b)" --quote "minimum necessary to accomplish the intended purpose"
ecfr-md compare 45 CFR 164.502 --since 2024-01-01 --json
```

`verify` exits with status 1 unless the quote is verified, so scripts can
check a draft's quotations.
