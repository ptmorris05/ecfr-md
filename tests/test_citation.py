import pytest

from ecfr_md import Citation, InvalidCitationError, InvalidParameterError


@pytest.mark.parametrize(
    ("text", "hierarchy", "paragraph", "canonical"),
    [
        (45, {}, (), "45 CFR"),
        ("Title 45", {}, (), "45 CFR"),
        ("45 CFR 164", {"part": "164"}, (), "45 CFR Part 164"),
        ("45 cfr part 164, subpart E", {"part": "164", "subpart": "E"}, (), None),
        ("45 CFR Pt. 164 Subpt. E", {"part": "164", "subpart": "E"}, (), None),
        (
            "45 C.F.R. § 164.502(a)(1)(i)(A)",
            {"section": "164.502"},
            ("a", "1", "i", "A"),
            "45 CFR 164.502(a)(1)(i)(A)",
        ),
        ("45 CFR 164.502 (a)", {"section": "164.502"}, ("a",), None),
        ("45CFR164.502(a)", {"section": "164.502"}, ("a",), "45 CFR 164.502(a)"),
        ("45 CFR 164.502(a).", {"section": "164.502"}, ("a",), "45 CFR 164.502(a)"),
        (
            "45 CFR Subtitle A Chapter I Subchapter C",
            {"subtitle": "A", "chapter": "I", "subchapter": "C"},
            (),
            None,
        ),
        (
            "45 CFR Appendix A to Subpart C of Part 164",
            {"part": "164", "appendix": "Appendix A to Subpart C of Part 164"},
            (),
            None,
        ),
        (
            "45 CFR Part 164 Appendix A to Subpart C of Part 164",
            {"part": "164", "appendix": "Appendix A to Subpart C of Part 164"},
            (),
            "45 CFR Appendix A to Subpart C of Part 164",
        ),
        (
            "50 CFR Table 1a to Part 660, Subpart C",
            {"part": "660", "appendix": "Table 1a to Part 660, Subpart C"},
            (),
            None,
        ),
        (
            "50 CFR Table 1a (North) to Part 660, Subpart D",
            {"part": "660", "appendix": "Table 1a (North) to Part 660, Subpart D"},
            (),
            None,
        ),
        (
            "12 CFR Supplement I to Part 1026",
            {"part": "1026", "appendix": "Supplement I to Part 1026"},
            (),
            None,
        ),
        (
            "14 CFR Table C to Part 117",
            {"part": "117", "appendix": "Table C to Part 117"},
            (),
            None,
        ),
        (
            "29 CFR Non-mandatory Appendix E to Subpart L of Part 1926",
            {
                "part": "1926",
                "appendix": "Non-mandatory Appendix E to Subpart L of Part 1926",
            },
            (),
            None,
        ),
        (
            "45 cfr appendix a to part 164",
            {"part": "164", "appendix": "Appendix a to part 164"},
            (),
            None,
        ),
        ("41 CFR 109-46", {"part": "109-46"}, (), "41 CFR Part 109-46"),
        ("41 CFR 109-46.000", {"section": "109-46.000"}, (), None),
        (  # Sections numbered unlike their part: 43 CFR Part 4100 holds 4110.1.
            "43 CFR Part 4100 4110.1(a)",
            {"part": "4100", "section": "4110.1"},
            ("a",),
            "43 CFR 4110.1(a)",
        ),
        (
            "17 CFR 210.3-07 - 210.3-08",
            {"section": "210.3-07 - 210.3-08"},
            (),
            None,
        ),
        (
            "17 CFR §§ 210.3-07\u2014210.3-08",
            {"section": "210.3-07 - 210.3-08"},
            (),
            "17 CFR 210.3-07 - 210.3-08",
        ),
        (  # Title 26 section numbers contain parentheses of their own.
            "26 CFR 1.401(k)-1(a)(2)",
            {"section": "1.401(k)-1"},
            ("a", "2"),
            None,
        ),
    ],
)
def test_parse_and_format(text, hierarchy, paragraph, canonical):
    citation = Citation.parse(text)
    assert citation.hierarchy == hierarchy
    assert citation.paragraph == paragraph
    if canonical:
        assert str(citation) == canonical
    assert str(Citation.parse(str(citation))) == str(citation)  # Round trip.


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("CFR 164", "must start with a title number"),
        ("See 45 CFR 164", "must start with a title number"),
        ("", "must start with a title number"),
        ("45 CFR banana", "Unexpected 'banana'"),
        ("45 CFR Part", "'Part' needs a value"),
    ],
)
def test_invalid_citations(text, message):
    with pytest.raises(InvalidCitationError, match=message):
        Citation.parse(text)


def test_keywords_refine_and_conflicts_are_rejected():
    citation = Citation.parse("45 CFR 164").with_hierarchy({"subpart": "E"})
    assert str(citation) == "45 CFR Part 164 Subpart E"
    with pytest.raises(InvalidParameterError, match="Conflicting part"):
        Citation.parse("45 CFR 164").with_hierarchy({"part": "160"})
