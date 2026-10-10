"""Search the eCFR, then fetch the matching sections as Markdown.

Run: uv run python examples/04_search.py
"""

from ecfr_md import ECFR, SearchOrder

with ECFR() as ecfr:
    found = ecfr.search.results(
        "minimum necessary",
        agency_slugs=["health-and-human-services-department"],
        order=SearchOrder.RELEVANCE,
        per_page=20,
    )
    print(found.meta.description, f"({found.meta.total_count} matches)\n")

    # Results include past versions of a section, so keep the first of each.
    citations = dict.fromkeys(
        f"{r.hierarchy.title} CFR {r.hierarchy.section}"
        for r in found.results
        if r.hierarchy.section  # Appendices have no section number.
    )
    for citation in list(citations)[:5]:
        section = ecfr.get(citation)
        print(f"{section.citation}: {section.heading}")
        print(f"  {len(section)} top-level paragraphs\n")
