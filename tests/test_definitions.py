"""Every defined term that governs a place, as the web reader underlines them."""

import pytest
from web_data import install

import ecfr_md


@pytest.fixture
def api(api):
    return install(api)


def test_terms_from_the_part_and_the_subchapter(ecfr):
    terms = {d.term: d for d in ecfr.definitions("45 CFR 164.104(a)")}
    # 164.108 names no scope, so it covers its part; 160.103 its subchapter.
    assert terms["Term A"].citation.startswith("45 CFR 164.108")
    assert terms["Covered entity"].citation.startswith("45 CFR 160.103")
    assert next(iter(terms)) == "Term A"  # Nearest first.


def test_definitions_section_scope_limits_it(ecfr):
    # Part 160's own sections are governed by 160.103 but not by 164.108.
    terms = {d.term for d in ecfr.definitions("45 CFR 160.101")}
    assert "Covered entity" in terms and "Term A" not in terms


def test_define_agrees_with_definitions(ecfr):
    (found, *_) = ecfr.define("covered entities", "45/164.102")
    assert found.citation.startswith("45 CFR 160.103")
    assert found in ecfr.definitions("45/164.102")
    assert ecfr_md.Definition.__doc__
