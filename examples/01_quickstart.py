"""Get regulation text and save it as Markdown.

Run: uv run python examples/01_quickstart.py
"""

from pathlib import Path

import ecfr_md

OUTPUT = Path(__file__).parent / "output"

# A citation in (almost) any common form.
section = ecfr_md.get("45 CFR 164.502")
print(section.to_markdown()[:500], "...\n")

# A single paragraph, down to the deepest level.
paragraph = ecfr_md.get("45 C.F.R. § 164.502(a)(1)(i)")
print(paragraph.citation, "->", paragraph.text, "\n")

# Title number plus keywords works too, and so does any date format.
part = ecfr_md.get(45, part="164", date="January 1, 2024")

# One Markdown file, hierarchy encoded as headings...
print("Saved", part.save(OUTPUT / "45-cfr-164.md"))

# ...or a directory tree with one file per section.
files = part.save(OUTPUT / "45-cfr-164", tree=True)
print(f"Saved {len(files)} files under", OUTPUT / "45-cfr-164")

# ecfr_md.save is shorthand for ecfr_md.get(...).save(...).
ecfr_md.save("45 CFR Part 160", OUTPUT / "45-cfr-160.md")
