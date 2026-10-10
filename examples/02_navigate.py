"""Walk the CFR hierarchy of a single response, without further API calls.

Run: uv run python examples/02_navigate.py
"""

from ecfr_md import ECFR, Level

with ECFR() as ecfr:
    part = ecfr.get("45 CFR Part 164")  # One request; everything below is local.

print(repr(part))

# Direct children, in document order.
for subpart in part:
    print(f"  {subpart.identifier}: {subpart.heading}")

# Any descendant by identifier (nearest match), with or without a level name.
privacy = part["Subpart E"]
section = part["§ 164.512"]
print("\n", repr(privacy), "\n", repr(section), sep="")

# Everything at one level, at any depth.
sections = list(privacy.iter(Level.SECTION))
print(f"\nSubpart E has {len(sections)} sections, e.g. {sections[0].citation}")

# Paragraphs nest (a) > (1) > (i) > (A), and are indexable the same way.
exception = section["(k)(1)(i)"]
print(f"\n{exception.citation}: {exception.heading}")
print("  parent:", exception.parent.citation)
print("  in section:", exception.section.citation)

# Find the paragraphs whose run-in heading mentions "law enforcement".
for paragraph in section.iter("paragraph"):
    if "law enforcement" in paragraph.heading.lower():
        print(f"  {paragraph.citation}: {paragraph.heading}")
