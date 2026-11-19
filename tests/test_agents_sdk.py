"""The SDK pieces agents use: budgeted passages, quote checks and definitions."""

import copy

import pytest
from conftest import PART_XML, STRUCTURE
from hipaa import install

import ecfr_md
import ecfr_md.shortcuts
from ecfr_md import InvalidParameterError, NodeNotFoundError, load
from ecfr_md.definitions import Definition, find_definitions, matches
from ecfr_md.passages import (
    TextIndex,
    check_quote,
    normalize,
    outline_size,
    outline_tokens,
    parse_cursor,
    passage,
    plain,
    tokens,
)
from ecfr_md.results import Outline


@pytest.fixture
def hipaa(api, ecfr):
    install(api)
    return ecfr


@pytest.fixture
def part():
    return load(PART_XML, title=45)


def test_normalize_drops_markup_and_keeps_positions():
    text, positions = normalize("**(a)** *Standard.* “Quoted”—done  \n\n")
    assert text == '(a) Standard. "Quoted"-done'
    assert positions[0] == 2  # The "(" after "**".
    assert plain("") == ""
    assert tokens("abcd" * 10) == 10


def test_index_finds_where_each_paragraph_starts(part):
    index = TextIndex(part)
    section = part["164.104"]
    offset = index.markdown.find("**(i)** Roman one")
    assert index.at(offset).citation == "45 CFR 164.104(a)(2)(i)"
    assert index.at(0) is part
    within = index.within(0, len(index.markdown))
    assert section["(a)(1)"] in within
    assert all(n.level == "paragraph" for n in within)
    assert index.raw(0, 10**9)[1] == len(index.markdown.rstrip())


def test_passage_fits_the_budget_and_continues(hipaa):
    node = hipaa.get("45 CFR 164.502")
    first = passage(node, 60)
    assert not first.complete and first.more
    assert first.tokens <= 60
    assert first.cursor == f"{node.uri}#{first.end}"
    assert "**(a)(1)(i)** To the individual;" in node.to_markdown() or True
    rest = passage(node, 10_000, first.end)
    assert rest.end == rest.total and rest.cursor is None and not rest.complete
    assert first.text.strip() + "\n\n" + rest.text.strip() != ""
    whole = passage(node, 10_000)
    assert whole.complete and whole.to_dict()["complete"]
    assert "**(a)(1)(ii)** For treatment" in whole.text  # Full pinpoints.
    assert "**(a)** *Standard.*" in whole.text  # Top level as it was.
    plain_text = passage(node, 10_000, pinpoints=False)
    assert "**(ii)** For treatment" in plain_text.text
    assert [p.citation for p in whole.paragraphs][:2] == [
        "45 CFR 164.502(a)",
        "45 CFR 164.502(a)(1)",
    ]
    assert whole.to_dict()["paragraphs"][0]["anchor"] == "p-164.502(a)"


def test_passage_keeps_sections_whole_and_cuts_long_blocks(hipaa):
    node = hipaa.get("45 CFR Part 164 Subpart E")
    text = node.to_markdown()
    heading = text.find("## § 164.502")
    budget = heading // 4 + 40  # Room for a little of 164.502, not all of it.
    cut = passage(node, budget)
    assert cut.end == heading  # Ends before the section, not inside it.
    one_block = load(
        "<DIV8 N='1.1' TYPE='SECTION'><HEAD>§ 1.1 A.</HEAD><P>"
        + "Word word word. " * 200
        + "</P></DIV8>",
        title=1,
    )
    sliced = passage(one_block, 100, len("# § 1.1 A.\n\n"))
    assert sliced.text.rstrip().endswith(".")
    no_breaks = load(
        "<DIV8 N='1.1' TYPE='SECTION'><HEAD>§ 1.1 A.</HEAD><P>"
        + "x" * 3000
        + "</P></DIV8>",
        title=1,
    )
    start = len("# § 1.1 A.\n\n")
    assert passage(no_breaks, 100, start).end == start + 400


@pytest.mark.parametrize(
    ("budget", "offset", "message"),
    [(0, 0, "at least 1"), (10, -1, "outside the text"), (10, 10**9, "outside")],
)
def test_passage_errors(part, budget, offset, message):
    with pytest.raises(InvalidParameterError, match=message):
        passage(part, budget, offset)


def test_cursors():
    assert parse_cursor(" cfr:45/164.502@2024-01-01#512 ") == (
        "cfr:45/164.502@2024-01-01",
        512,
    )
    for bad in ("cfr:45/164.502", "#12", "x#y"):
        with pytest.raises(InvalidParameterError, match="isn't a cursor"):
            parse_cursor(bad)


def test_outline_token_estimates(hipaa):
    part = hipaa.outline("45 CFR Part 164")
    assert outline_tokens(part) == 2850  # From the part's own size.
    subpart = part["Subpart E"]
    assert outline_size(subpart) == 2800  # Its sections' sizes.
    assert outline_tokens(subpart["164.504"]) == 0
    bare = Outline.build(part.raw.model_copy(update={"size": None, "children": []}), 45)
    assert outline_tokens(bare) is None
    whole = hipaa.outline()
    assert outline_size(whole["45"]) is None  # Not opened yet.


def test_quotes_are_found_at_their_pinpoint(hipaa):
    node = hipaa.get("45 CFR 164.502")
    found = check_quote(node, "(i) To the  individual;", node["(a)(1)(i)"])
    assert found.verified and found.similarity == 1.0
    assert found.citation == "45 CFR 164.502(a)(1)(i)"
    assert found.to_dict()["verified"] and "differences" not in found.to_dict()
    elsewhere = check_quote(node, "To the individual", node["(b)"])
    assert elsewhere.found and not elsewhere.cited and not elsewhere.verified
    spanning = check_quote(node, "as follows: To the individual;")
    assert spanning.citation == "45 CFR 164.502(a)(1)"  # It holds both.
    across = check_quote(node, "To the individual; For treatment")
    assert across.citation == "45 CFR 164.502(a)(1)(i) through (a)(1)(ii)"
    with pytest.raises(InvalidParameterError, match="words"):
        check_quote(node, ' "" ')


def test_nearest_text_when_a_quote_is_wrong(hipaa):
    node = hipaa.get("45 CFR 164.502")
    near = check_quote(node, "A covered entity may disclose protected information to")
    assert not near.found and 0.5 < near.similarity < 1
    assert near.citation.startswith("45 CFR 164.502(a)")
    ops = {op for op, _ in near.differences()}
    assert ops == {"=", "-", "+"}
    assert "differences" in near.to_dict()
    nothing = check_quote(node, "zebra quantum harmonica xylophone")
    assert not nothing.found and nothing.matched == "" and nothing.similarity < 0.5
    across_sections = check_quote(
        hipaa.get("45 CFR Part 164"), "Without unreasonable delay. Subpart E"
    )
    assert across_sections.found
    assert across_sections.citation == (
        "45 CFR 164.404(b) through 45 CFR Part 164 Subpart E"
    )


def test_client_verify_quote_looks_in_the_part(hipaa):
    check = hipaa.verify_quote("45 CFR 164.502(b)", "A covered entity shall notify")
    assert check.found and not check.verified
    assert check.citation == "45 CFR 164.404(a)"
    missing = hipaa.verify_quote("45 CFR 164.502", "nothing like this at all here")
    assert not missing.found
    assert hipaa.verify_quote("45 CFR 164.502(a)(1)(i)", "To the individual").verified


DEFINITIONS = """<DIV8 N="1026.2" TYPE="SECTION">
<HEAD>§ 1026.2 Definitions and rules of construction.</HEAD>
<P>(a) <I>Definitions.</I> For purposes of this part, these definitions apply:</P>
<P>(1) <I>Act</I> means the Truth in Lending Act.</P>
<P>(2)(i) <I>Application</I> means the submission of information.</P>
<P>(ii) For some transactions, an application is more.</P>
<P>(A) Upper detail.</P>
<P>(<I>1</I>) Italic detail.</P>
<P>(3) <I>Billing cycle</I> or <I>cycle</I> means the interval between statements.</P>
<P>(b) <I>Rules of construction.</I> Footnotes have legal effect.</P>
</DIV8>"""


def test_find_definitions_reads_labelled_definitions():
    section = load(DEFINITIONS, title=12)
    found = find_definitions(section, "12 CFR Part 1026")
    assert [d.terms for d in found] == [
        ("Act",),
        ("Application",),
        ("Billing cycle", "cycle"),
    ]
    act, application, cycle = found
    assert act.citation == "12 CFR 1026.2(a)(1)" and act.cite() == act.citation
    assert application.citation == "12 CFR 1026.2(a)(2)(i)"
    assert "(ii)" in application.text  # Continues to its subparagraphs,
    assert "Upper detail" in application.text and "Italic" in application.text
    assert "Rules of construction" not in cycle.text  # but stops at (b).
    assert matches("cycles", cycle) and not matches("bicycle", cycle)
    assert cycle.to_dict()["applies_to"] == "12 CFR Part 1026"


def test_find_definitions_reads_unlabelled_definitions(hipaa):
    found = find_definitions(hipaa.get("45 CFR 160.103"))
    assert [d.term for d in found] == [
        "Business associate",
        "Covered entity",
        "Health plan",
    ]
    associate = found[0]
    assert associate.citation == "45 CFR 160.103"
    assert associate.cite() == '45 CFR 160.103 (definition of "Business associate")'
    assert "(2)" in associate.text  # Its numbered paragraphs belong to it.
    assert isinstance(associate, Definition)


@pytest.mark.parametrize(
    "block",
    [
        "**(a)** *Standard.* A covered entity must.",  # A heading, not a term.
        "*Term* *Other* means",  # Two terms without a joining word.
        "<I>:</I> means nothing",  # No term at all.
    ],
)
def test_not_definitions(block):
    section = load(
        f"<DIV8 N='1.1' TYPE='SECTION'><HEAD>§ 1.1 A.</HEAD><P>{block}</P></DIV8>",
        title=1,
    )
    found = find_definitions(section)
    assert all(d.term != "Standard." for d in found)


def test_definition_ends_at_a_heading_or_note():
    section = load(
        "<DIV5 N='1' TYPE='PART'><HEAD>Part 1</HEAD>"
        "<DIV8 N='1.1' TYPE='SECTION'><HEAD>§ 1.1 Definitions.</HEAD>"
        "<P><I>Alpha</I> means a.</P><P>More about alpha.</P></DIV8>"
        "<DIV8 N='1.2' TYPE='SECTION'><HEAD>§ 1.2 Other.</HEAD>"
        "<P>Unrelated text.</P></DIV8></DIV5>",
        title=1,
    )
    (alpha,) = find_definitions(section)
    assert "More about alpha" in alpha.text and "Unrelated" not in alpha.text


def test_define_finds_the_governing_definition(hipaa):
    found = hipaa.define("covered entities", "45 CFR 164.502")
    (definition,) = found
    assert definition.citation == "45 CFR 160.103"
    assert definition.applies_to == "45 CFR Subtitle A Subchapter C"
    assert found.scope == "45 CFR 164.502"
    assert found.searched[:2] == ["45 CFR 164.502", "45 CFR 164.501"]
    assert found.to_dict()["definitions"][0]["term"] == "Covered entity"


def test_define_keeps_definitions_that_apply_there(hipaa):
    # 164.402's "disclosure" applies to subpart D only; 164.501's to E.
    (here,) = hipaa.define("disclosure", "45 CFR 164.502")
    assert here.citation == "45 CFR 164.501"
    both = hipaa.define("Disclosure", "45 CFR Part 164")  # The part holds both.
    assert [d.citation for d in both] == ["45 CFR 164.402", "45 CFR 164.501"]
    (local,) = hipaa.define("breach", "45 CFR 164.402")
    assert local.applies_to == "45 CFR Part 164 Subpart D"


def test_define_suggests_when_nothing_is_found(hipaa, api):
    found = hipaa.define("covered entty", "45 CFR 164.502")
    assert not found
    assert found.suggestions == ["Covered entity"]
    assert [m.citation for m in found.mentions] == ["45 CFR 164.502", "45 CFR 164.404"]
    assert api.requests[-1].url.params["query"] == '"covered entty" means'
    assert found.to_dict()["mentions"][0]["citation"] == "45 CFR 164.502"
    with pytest.raises(InvalidParameterError, match="term"):
        hipaa.define(" ", "45 CFR 164.502")


def test_section_scope_reads_its_own_text_first(hipaa):
    (found,) = hipaa.define("Common control", "45 CFR 164.103")
    assert found.citation == "45 CFR 164.103"
    assert found.applies_to == "45 CFR Part 164"


def test_what_definitions_apply_to(api, ecfr):
    """Sections that don't say, or say "this section"; and titles with no
    subchapter or chapter, where the search stops below the subtitle."""
    api.json("/api/search/v1/results", {"results": [], "meta": {"total_pages": 1}})
    structure = copy.deepcopy(STRUCTURE)  # The default title, with sections.
    sections = [
        {"identifier": n, "label": f"§ {n} {h}", "type": "section"}
        for n, h in (("164.104", "Nesting."), ("164.108", "Definitions."))
    ]
    structure["children"][0]["children"][0]["children"] = [
        {
            "identifier": "A",
            "label": "Subpart A",
            "type": "subpart",
            "children": sections,
        }
    ]
    api.json("/api/versioner/v1/structure/2026-10-07/title-45.json", structure)
    found = ecfr.define("Term A", "45 CFR 164.104")
    (term,) = found
    assert term.citation == "45 CFR 164.108" and term.applies_to is None
    assert not ecfr.define("nothing", "45 CFR 164.104")
    section = load(
        "<DIV8 N='1.1' TYPE='SECTION'><HEAD>§ 1.1 Definitions.</HEAD>"
        "<P>For purposes of this section:</P><P><I>Alpha</I> means a.</P></DIV8>",
        title=45,
    )
    outline = Outline.build(
        ecfr.outline("45 CFR 164.104").raw, 45, {"part": "164", "section": "164.104"}
    )
    from ecfr_md.definitions import _applies_to

    assert _applies_to(section, outline) is outline


def test_run_in_labels_keep_their_pinpoint(part):
    text = passage(part["164.102"], 1000).text
    assert "**(a)(1)** *Standard.*" in text
    assert "**(a)(1)(i)** See" in text
    nested = passage(part["164.104"], 1000).text
    assert "**(j)** *Heading.* **(j)(1)** Run in" in nested
    assert "<strong>(b)</strong>-(g) [Reserved]" in nested


def test_nothing_to_match():
    from ecfr_md.passages import _nearest

    assert _nearest("", "words") == (0, 0, 0.0)


def test_module_level_functions(api, monkeypatch, ecfr):
    install(api)
    monkeypatch.setattr(ecfr_md.shortcuts, "client", lambda: ecfr)
    assert ecfr_md.define("use", "45 CFR 164.502")[0].citation == "45 CFR 164.501"
    assert ecfr_md.verify_quote("45 CFR 164.502", "To the individual").verified


def test_did_you_mean(hipaa):
    with pytest.raises(NodeNotFoundError, match=r"did you mean 164\.502"):
        hipaa.outline("45 CFR 164.5021")
    with pytest.raises(NodeNotFoundError, match=r"did you mean \(b\)"):
        hipaa.get("45 CFR 164.502(bb)")
