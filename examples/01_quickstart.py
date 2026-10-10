"""Get regulation text and save it as Markdown.

Run: uv run python examples/01_quickstart.py
"""

from pathlib import Path

from ecfr_md import ECFR

OUTPUT = Path(__file__).parent / "output"

with ECFR() as ecfr:
    # A citation in (almost) any common form.
    section = ecfr.get("45 CFR 164.502")
    print(section.to_markdown()[:500], "...\n")

    # A single paragraph, down to the deepest level.
    paragraph = ecfr.get("45 C.F.R. § 164.502(a)(1)(i)")
    print(paragraph.citation, "->", paragraph.text, "\n")

    # Title number plus keywords works too, and so does any date format.
    part = ecfr.get(45, part="164", date="January 1, 2024")

    # One Markdown file, hierarchy encoded as headings...
    print("Saved", part.save(OUTPUT / "45-cfr-164.md")[0])

    # ...or a directory tree with one file per section.
    files = part.save(OUTPUT / "45-cfr-164", tree=True)
    print(f"Saved {len(files)} files under", OUTPUT / "45-cfr-164")

    # ecfr.save is shorthand for ecfr.get(...).save(...).
    ecfr.save("45 CFR Part 160", OUTPUT / "45-cfr-160.md")
