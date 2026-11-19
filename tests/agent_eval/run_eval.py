"""Agent tasks against the live eCFR, through the MCP server.

Not run in CI. Each task is the sequence of tool calls a capable agent
makes for a realistic request; the script checks the answer is grounded
(the right citation, a verified quote) and reports, per task, the tool
calls and the tokens the agent reads (the Markdown content; four
characters to a token) next to the structured JSON it could have read
instead.

    uv run python tests/agent_eval/run_eval.py [--no-cache]

It uses the user cache and concurrency 2, so a second run makes few
requests. Results go to stdout as a table and to agent_eval.json.
"""

import argparse
import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastmcp import Client

from ecfr_md import ECFR, Settings
from ecfr_md.mcp import server


@dataclass
class Run:
    """One task's calls and what they cost."""

    client: Client
    calls: list[tuple[str, int, int]] = field(default_factory=list)
    checks: list[tuple[str, bool]] = field(default_factory=list)

    async def call(self, name: str, **arguments) -> tuple[str, dict]:
        result = await self.client.call_tool(name, arguments, raise_on_error=False)
        text = "\n".join(c.text for c in result.content if hasattr(c, "text"))
        data = result.structured_content or {}
        self.calls.append((name, len(text) // 4, len(json.dumps(data)) // 4))
        return text, data

    def check(self, what: str, ok: bool) -> None:
        self.checks.append((what, bool(ok)))


async def find_minimum_necessary(run: Run) -> None:
    """Where does HIPAA set the "minimum necessary" standard? Quote it."""
    _, hits = await run.call(
        "search", query="minimum necessary", within="45 CFR Part 164", limit=5
    )
    run.check(
        "search finds 164.502",
        any(h["citation"] == "45 CFR 164.502" for h in hits["results"]),
    )
    _, text = await run.call("read", citation="45 CFR 164.502(b)", budget_tokens=1500)
    run.check("read gives (b)", text["citation"] == "45 CFR 164.502(b)")
    _, check = await run.call(
        "verify_quote",
        citation="45 CFR 164.502(b)",
        text="a covered entity or business associate must make reasonable efforts "
        "to limit protected health information to the minimum necessary",
    )
    run.check("quote verified", check.get("verified"))


async def navigate_to_law_enforcement(run: Run) -> None:
    """From the title down: what may be disclosed for law enforcement?"""
    _, title = await run.call("outline", citation="45")
    chapter = next(e for e in title["entries"] if "Administrative Data" in e["heading"])
    _, division = await run.call("outline", citation=chapter["address"], depth=1)
    run.check(
        "subchapter outline has Part 164",
        any(e["address"] == "45/part-164" for e in division["entries"]),
    )
    _, part = await run.call("outline", citation="45 CFR Part 164 Subpart E", depth=1)
    target = next(e for e in part["entries"] if e["citation"] == "45 CFR 164.512")
    run.check("outline sizes 164.512", (target["tokens"] or 0) > 1000)
    _, text = await run.call("read", citation="45 CFR 164.512(f)", budget_tokens=2500)
    pages = 1
    while text.get("cursor") and pages < 4:
        _, text = await run.call("read", cursor=text["cursor"], budget_tokens=2500)
        pages += 1
    run.check("read through (f)", text["cursor"] is None)


async def what_changed(run: Run) -> None:
    """What changed in 45 CFR 164.502 in its latest amendment?"""
    _, history = await run.call("history", citation="45 CFR 164.502", limit=5)
    latest = history["versions"][0]["effective"]
    day = __import__("datetime").date.fromisoformat(latest) - __import__(
        "datetime"
    ).timedelta(days=1)
    _, diff = await run.call(
        "compare", citation="45 CFR 164.502", from_date=str(day), to_date=latest
    )
    run.check("redline has differences", len(diff["differences"]) > 0)
    run.check(
        "amendment listed", any(a["effective"] == latest for a in diff["amendments"])
    )


async def define_business_associate(run: Run) -> None:
    """What is a "business associate" for 45 CFR 164.504?"""
    _, found = await run.call(
        "define", term="business associate", scope="45 CFR 164.504"
    )
    run.check(
        "defined in 160.103",
        any(d["section"] == "45 CFR 160.103" for d in found["definitions"]),
    )


async def define_billing_cycle(run: Run) -> None:
    """Regulation Z: what is a "billing cycle" for 12 CFR 1026.7?"""
    _, found = await run.call("define", term="billing cycle", scope="12 CFR 1026.7")
    run.check(
        "defined in 1026.2(a)(4)",
        any(d["citation"] == "12 CFR 1026.2(a)(4)" for d in found["definitions"]),
    )


async def catch_a_bad_quote(run: Run) -> None:
    """A draft says § 164.502(a) permits disclosure "for any purpose" — check."""
    _, check = await run.call(
        "verify_quote",
        citation="45 CFR 164.502(a)",
        text="A covered entity may use or disclose protected health information "
        "for any purpose",
    )
    run.check("not verified", not check["verified"])
    run.check("nearest text offered", check["similarity"] > 0.5)


async def resolve_and_follow(run: Run) -> None:
    """Resolve a Bluebook cite, then follow what it cites."""
    _, resolved = await run.call("resolve_citation", text="45 C.F.R. § 164.506(c)(1)")
    run.check("resolved", resolved["places"][0]["exists"] is True)
    _, refs = await run.call("references", citation="45 CFR 164.506")
    run.check(
        "cites 164.508", any(r["address"] == "45/164.508" for r in refs["references"])
    )


async def typo_recovery(run: Run) -> None:
    """A typo'd citation: the error says what exists."""
    text, _ = await run.call("read", citation="45 CFR 164.5122")
    run.check("did you mean", "did you mean 164.512" in text)


TASKS: list[tuple[str, Callable[[Run], Awaitable[None]]]] = [
    ("find + quote", find_minimum_necessary),
    ("navigate down", navigate_to_law_enforcement),
    ("what changed", what_changed),
    ("define (HIPAA)", define_business_associate),
    ("define (Reg Z)", define_billing_cycle),
    ("catch bad quote", catch_a_bad_quote),
    ("resolve + refs", resolve_and_follow),
    ("typo recovery", typo_recovery),
]


async def main(cache: bool) -> None:
    ecfr = ECFR(Settings(concurrency=2, cache=cache))
    rows = []
    async with Client(server(ecfr)) as client:
        for name, task in TASKS:
            run = Run(client)
            start = time.perf_counter()
            await task(run)
            rows.append(
                {
                    "task": name,
                    "calls": len(run.calls),
                    "markdown_tokens": sum(c[1] for c in run.calls),
                    "json_tokens": sum(c[2] for c in run.calls),
                    "seconds": round(time.perf_counter() - start, 1),
                    "passed": all(ok for _, ok in run.checks),
                    "checks": run.checks,
                    "tools": [c[0] for c in run.calls],
                }
            )
    print(f"{'task':18} calls  md-tok  json-tok   secs  ok")
    for r in rows:
        print(
            f"{r['task']:18} {r['calls']:5} {r['markdown_tokens']:7} "
            f"{r['json_tokens']:9} {r['seconds']:6}  {'yes' if r['passed'] else 'NO'}"
        )
        for what, ok in r["checks"]:
            if not ok:
                print(f"    failed: {what}")
    Path("agent_eval.json").write_text(json.dumps(rows, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-cache", action="store_true")
    asyncio.run(main(cache=not parser.parse_args().no_cache))
