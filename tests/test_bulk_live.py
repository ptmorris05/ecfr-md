"""GovInfo's bulk files render as the eCFR API's text does, section by section.

Run with: uv run pytest -m network. Each title costs one request to each host.
"""

import re

import pytest

from ecfr_md import ECFR, Settings, markdown
from ecfr_md.document import Division

pytestmark = pytest.mark.network

TITLES = (1, 3, 10, 45)
# Known differences between the two publications, not normalization gaps.
ALLOWED = {
    (10, "SECTION", "51.51"): "bulk lacks a table's headnote",
    (10, "APPENDIX", "Appendix C1 to Subpart R of Part 431"): "emphasis the API drops",
    (10, "SECTION", "460.102"): "emphasis the API drops",
    (10, "APPENDIX", "Appendix C to Subpart G of Part 431"): "a table column",
}
EMPTY_CELLS = re.compile(r"(?:\|\s+)+\|$")
RULE = re.compile(r"^(>?\s*)\|(?: --- \|)+$")
BLANK = re.compile(r"(?:\\_)+")


def loose(text: str) -> str:
    """Rendered text without differences in source data that aren't ours to
    fix: lengths of fill-in blanks, and empty table columns."""
    lines = []
    for line in BLANK.sub(r"\\_", text).splitlines():
        line = RULE.sub(r"\1| --- |", line)
        lines.append(EMPTY_CELLS.sub("|", line))
    return "\n".join(lines)


def sections(node: Division) -> dict[tuple[str, str], str]:
    found: dict[tuple[str, str], str] = {}
    for part in node.iter("part"):
        for el in part.element.iter():
            if el.get("TYPE") in ("SECTION", "APPENDIX"):
                key = (el.get("TYPE", ""), el.get("N", ""))
                found[key] = found.get(key, "") + markdown.render(el)
    return found


@pytest.mark.parametrize("title", TITLES)
def test_bulk_text_renders_as_the_apis(title, tmp_path):
    texts = {}
    for source in ("api", "bulk"):
        settings = Settings(source=source, cache_dir=tmp_path / source)
        with ECFR(settings) as ecfr:
            node = ecfr.get(title)
            texts[source] = sections(node)
            if source == "bulk" and "govinfo.gov" not in node.provenance["api"]:
                pytest.skip(f"GovInfo's copy of title {title} isn't current")
    api, bulk = texts["api"], texts["bulk"]
    assert bulk.keys() == api.keys()
    differ = [
        key
        for key in api
        if loose(api[key]) != loose(bulk[key]) and (title, *key) not in ALLOWED
    ]
    assert differ == [], f"{len(differ)} of {len(api)} differ: {differ[:10]}"
