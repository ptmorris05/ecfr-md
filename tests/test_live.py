"""Smoke tests against the real eCFR API. Run with: uv run pytest -m network"""

import pytest

from cfr_md import ECFR, NotFoundError

pytestmark = pytest.mark.network


@pytest.fixture(scope="module")
def ecfr():
    with ECFR() as client:
        yield client


def test_section_markdown(ecfr):
    text = ecfr.markdown(45, part="164", section="164.502")
    assert text.startswith("# § 164.502 Uses and disclosures of protected health")


def test_every_json_endpoint_parses(ecfr):
    assert ecfr.admin.agencies().agencies
    assert ecfr.admin.corrections(title=45).ecfr_corrections
    assert ecfr.admin.title_corrections(45).ecfr_corrections
    assert ecfr.search.results("minimum necessary", per_page=2).results
    assert ecfr.search.count("minimum necessary").meta.total_count
    assert ecfr.search.summary("minimum necessary").meta.description
    assert ecfr.search.daily_counts("minimum necessary").dates
    assert ecfr.search.title_counts("minimum necessary").titles
    assert ecfr.search.hierarchy_counts("minimum necessary").count.value
    ecfr.search.suggestions("minimum")
    assert ecfr.versioner.titles().titles
    assert ecfr.versioner.ancestry(45, part="164").ancestors
    assert ecfr.versioner.structure(45).children
    assert ecfr.versioner.versions(45, part="164").content_versions


def test_missing_content_is_not_found(ecfr):
    with pytest.raises(NotFoundError, match="No matching content found"):
        ecfr.versioner.full(45, part="999")
