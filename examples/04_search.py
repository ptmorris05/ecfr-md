"""Search the eCFR, then fetch the matching sections as Markdown.

Run: uv run python examples/04_search.py
"""

import ecfr_md

matches = ecfr_md.search(
    "minimum necessary",
    agency_slugs=["health-and-human-services-department"],
    limit=5,
)
print(f"{len(matches)} of {matches.total} matches\n")

for match in matches:
    print(f"{match.citation}: {match.heading}")
    print(f"  {match.excerpt[:120]}")
    section = ecfr_md.get(match.citation)
    print(f"  {len(section)} top-level paragraphs\n")

# The search endpoints themselves, with every option the API has.
with ecfr_md.ECFR() as ecfr:
    print(ecfr.api.search.count("minimum necessary").meta.total_count, "versions")
