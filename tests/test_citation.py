import re

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
        ("CFR 164", "title number"),
        ("See 45 CFR 164", "title number"),
        ("", "title number"),
        ("45 CFR banana", "Unexpected 'banana'"),
        ("45 CFR Part", "'Part' needs a value"),
    ],
)
def test_invalid_citations(text, message):
    with pytest.raises(InvalidCitationError, match=message):
        Citation.parse(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("\u201c45 CFR 164.502(a)\u201d", "45 CFR 164.502(a)"),
        ("\u201845 CFR 164\u2019", "45 CFR Part 164"),
        ('"45 CFR 164.502"', "45 CFR 164.502"),
        ("\ufeff45\u200b CFR 164.502\u200b", "45 CFR 164.502"),
        ("12 CFR 1026 Supp. I", "12 CFR Supplement I to Part 1026"),
        ("12 CFR pt. 1026, Supp. I", "12 CFR Supplement I to Part 1026"),
        ("12 cfr part 1026 supp I", "12 CFR Supplement I to Part 1026"),
        ("12 CFR Supp. I to pt. 1026", "12 CFR Supplement I to Part 1026"),
        ("12 C.F.R. pt. 1026, app. J", "12 CFR Appendix J to Part 1026"),
        ("12 CFR 1026 App. J", "12 CFR Appendix J to Part 1026"),
        ("12 CFR Part 1026 Appendix J", "12 CFR Appendix J to Part 1026"),
        (
            "45 CFR Part 164 Subpt. C app. A",
            "45 CFR Appendix A to Subpart C of Part 164",
        ),
        (
            "45 CFR app. A to subpt. C of pt. 164",
            "45 CFR Appendix A to Subpart C of Part 164",
        ),
        ("45 CFR 164.500 et seq.", "45 CFR 164.500"),
        ("45 CFR Part 164 et seq", "45 CFR Part 164"),
        ("45 CFR subpart E of part 164", "45 CFR Part 164 Subpart E"),
        ("Part 164 of Title 45", "45 CFR Part 164"),
        ("Section 164.502 of Title 45.", "45 CFR 164.502"),
        ("Subpart E of Part 164, of Title 45", "45 CFR Part 164 Subpart E"),
    ],
)
def test_pasted_citations(text, expected):
    assert str(Citation.parse(text)) == expected


@pytest.mark.parametrize(
    ("text", "message"),
    [
        (
            "45 CFR 164.502 (2023)",
            r'^"\(2023\)" looks like a year; for text as of a date use --date 2023 '
            r'\(Python: date="2023"\)$',
        ),
        ("45 CFR 164.502(1999).", r'^"\(1999\)" looks like a year'),
        ("45 CRF 164.502", r"^\"CRF\" isn't recognised; did you mean CFR\?$"),
        ("45 C.R.F. 164", r"^\"C\.R\.F\.\" isn't recognised; did you mean CFR\?$"),
        ("45CFT164.502", r"^\"CFT\" isn't recognised"),
        (
            "45 CFR 164.502(a",
            r"^unbalanced parentheses in '45 CFR 164\.502\(a'; each \( needs a",
        ),
        ("45 CFR 164.502)(a", r"^unbalanced parentheses"),
        (
            "164.502 45 CFR",
            r"^'164\.502' needs a title number first, e\.g\. 45 CFR 164\.502$",
        ),
        (
            "164.502 17 C.F.R.",
            r"^'164\.502' needs a title number first, e\.g\. 17 CFR 164\.502$",
        ),
    ],
)
def test_pasted_mistakes_are_explained(text, message):
    with pytest.raises(InvalidCitationError, match=message):
        Citation.parse(text)


RANGE = (
    "is a range of several sections, but a citation names one. Cite the part or "
    "subpart that contains them (e.g. 45 CFR Part 164), or put one citation per "
    "line in a file and use --from FILE"
)
LIST = "lists several sections, but a citation names one"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("45 CFR 164.500-164.534", RANGE),
        ("45 CFR 164.502\u2013164.506", RANGE),
        ("45 CFR 164.502 \u2013 164.506", RANGE),
        ("45 CFR 164.500 through 164.534", RANGE),
        ("45 CFR §§ 164.500 to 164.534", RANGE),
        ("45 CFR §§ 164.502, 164.506", LIST),
        ("45 CFR 164.502 and 164.506", LIST),
        ("45 CFR 164.502 164.506", LIST),
        ("43 CFR Part 4100 4110.1 & 4110.2", "e.g. 43 CFR Part 4100"),
        ("45 CFR 164.502, 164.506", "Citation.parse_many splits a list"),
    ],
)
def test_ranges_and_lists_are_explained(text, message):
    with pytest.raises(InvalidCitationError, match=re.escape(message)):
        Citation.parse(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("45 CFR §§ 164.502, 164.506", ["45 CFR 164.502", "45 CFR 164.506"]),
        ("45 CFR 164.502 and 164.506(a).", ["45 CFR 164.502", "45 CFR 164.506(a)"]),
        (
            "\u201c45 C.F.R. 164.502, 164.504, and 164.506\u201d",
            ["45 CFR 164.502", "45 CFR 164.504", "45 CFR 164.506"],
        ),
        ("45 CFR 164.502 or 164.506", ["45 CFR 164.502", "45 CFR 164.506"]),
        ("45 CFR 164.502", ["45 CFR 164.502"]),
        ("12 CFR pt. 1026, Supp. I", ["12 CFR Supplement I to Part 1026"]),
        ("17 CFR 210.3-07 - 210.3-08", ["17 CFR 210.3-07 - 210.3-08"]),
        (45, ["45 CFR"]),
    ],
)
def test_parse_many_expands_lists(text, expected):
    assert [str(c) for c in Citation.parse_many(text)] == expected


def test_parse_many_rejects_ranges():
    with pytest.raises(InvalidCitationError, match="range"):
        Citation.parse_many("45 CFR 164.500-164.534")


def test_keywords_refine_and_conflicts_are_rejected():
    citation = Citation.parse("45 CFR 164").with_hierarchy({"subpart": "E"})
    assert str(citation) == "45 CFR Part 164 Subpart E"
    with pytest.raises(InvalidParameterError, match="Conflicting part"):
        Citation.parse("45 CFR 164").with_hierarchy({"part": "160"})
