"""Exporting nodes and downloads: Node.export, save(format=) and the CLI."""

import datetime as dt
import json
import sys
import zipfile

import docx
import pytest
from pypdf import PdfReader

import ecfr_md
from ecfr_md import InvalidParameterError, MissingDependencyError, load
from ecfr_md.markdown import element_events, file_events
from ecfr_md.render.export import render, save_events

DATE = dt.date(2026, 10, 7)
TITLE_XML = """<DIV1 N="45" TYPE="TITLE"><HEAD>Title 45—Public Welfare</HEAD>
<HD1>Editorial note</HD1>
<DIV2 N="A" TYPE="SUBTITLE"><HEAD>SUBTITLE A</HEAD>
<DIV3 N="I" TYPE="CHAPTER"><HEAD>CHAPTER I</HEAD>
<DIV5 N="160" TYPE="PART"><HEAD>PART 160—GENERAL</HEAD>
<DIV7 N="ECFR1" TYPE="SUBJGRP"><HEAD>Definitions</HEAD>
<DIV8 N="160.101" TYPE="SECTION"><HEAD>§ 160.101 Basis.</HEAD><P>(a) Text.</P></DIV8>
</DIV7>
</DIV5>
<DIV7 N="ECFR2" TYPE="SUBJGRP"><HEAD>Forms</HEAD>
<DIV9 N="Appendix A to Chapter I" TYPE="APPENDIX">
<HEAD>Appendix A to Chapter I—Forms</HEAD><P>Form.</P></DIV9>
</DIV7>
</DIV3>
</DIV2>
</DIV1>"""


@pytest.fixture
def part(part_xml):
    return load(part_xml, title=45, date=DATE, hierarchy={"part": "164"})


def names(paths, root):
    return sorted(p.relative_to(root).as_posix() for p in paths)


def test_export_infers_the_format(part, tmp_path):
    for suffix in ("txt", "html", "json", "epub", "docx", "pdf"):
        file = part.export(tmp_path / f"part.{suffix}")
        assert file == tmp_path / f"part.{suffix}" and file.stat().st_size
    assert (tmp_path / "part.txt").read_text(encoding="utf-8").startswith("---\n")
    assert (
        json.loads((tmp_path / "part.json").read_text(encoding="utf-8"))["level"]
        == "part"
    )
    assert zipfile.ZipFile(tmp_path / "part.epub").namelist()[0] == "mimetype"
    assert (
        docx.Document(str(tmp_path / "part.docx")).paragraphs[0].style.name
        == "Heading 1"
    )  # type: ignore[union-attr]
    assert len(PdfReader(tmp_path / "part.pdf").outline) == 2
    md = part.export(tmp_path / "part.md")
    assert md.read_text(encoding="utf-8").startswith("---\n")
    assert (
        part.save(tmp_path / "plain.txt").read_text(encoding="utf-8").startswith("---")
    )
    assert not list(tmp_path.glob(".*.part"))  # Written atomically.


def test_export_to_a_directory_and_by_format(part, tmp_path):
    assert part.export(tmp_path, "html").name == "45_part-164.html"
    assert part["164.102"].export(f"{tmp_path}/new/", "pdf").name == "45_164.102.pdf"
    file = part.export(tmp_path / "x.md", "json")
    assert json.loads(file.read_text(encoding="utf-8"))["address"] == "45/part-164"
    paragraph = part["164.104(a)"]
    assert paragraph.export(tmp_path, "text", tree=True) == [
        tmp_path / "45_164.104_a.txt"
    ]
    with pytest.raises(InvalidParameterError, match="format"):
        part.export(tmp_path / "x.rtf")


def test_html_and_text_trees(part, tmp_path):
    files = part.export(tmp_path / "site", "html", tree=True)
    assert names(files, tmp_path / "site") == [
        "part-164/index.html",
        "part-164/subpart-A/164.102.html",
        "part-164/subpart-A/164.104.html",
        "part-164/subpart-A/164.106.html",
        "part-164/subpart-A/164.108.html",
        "part-164/subpart-A/index.html",
        "part-164/subpart-C/appendix-A.html",
    ]
    index = (tmp_path / "site/part-164/index.html").read_text(encoding="utf-8")
    assert (
        '<li><a href="subpart-A/index.html">Subpart A—General Provisions</a></li>'
        in index
    )
    assert 'class="up"' not in index
    assert '<section id="part-164" class="part">' in index
    sub = (tmp_path / "site/part-164/subpart-A/index.html").read_text(encoding="utf-8")
    assert '<a class="up" href="../index.html">Contents</a>' in sub
    assert '<a href="164.102.html">' in sub
    page = (tmp_path / "site/part-164/subpart-A/164.104.html").read_text(
        encoding="utf-8"
    )
    assert '<a class="up" href="index.html">' in page
    assert '<meta name="cfr:citation" content="45 CFR 164.104" />' in page
    texts = part.export(tmp_path / "text", "text", tree=True)
    assert len(texts) == 7
    leaf = (tmp_path / "text/part-164/subpart-A/164.102.txt").read_text(
        encoding="utf-8"
    )
    assert leaf.startswith('---\ncitation: "45 CFR 164.102"\n')
    single = part["164.102"].export(tmp_path / "one", "html", tree=True)
    assert names(single, tmp_path / "one") == ["164.102.html"]
    assert 'class="up"' not in single[0].read_text(encoding="utf-8")


def test_trees_of_parts(tmp_path):
    title = load(TITLE_XML, title=45, date=DATE)
    files = title.export(tmp_path / "t", "json", tree=True)
    assert names(files, tmp_path / "t") == [
        "title-45/subtitle-A/chapter-I/appendix-A.json",
        "title-45/subtitle-A/chapter-I/part-160.json",
    ]
    part = json.loads(files[0].read_text(encoding="utf-8"))
    assert part["citation"] == "45 CFR Part 160"
    assert part["children"][0]["level"] == "subject_group"
    subpart = load(
        "<DIV6 N='A' TYPE='SUBPART'><HEAD>Subpart A</HEAD><DIV8 N='1.1' TYPE='SECTION'>"
        "<HEAD>§ 1.1 X.</HEAD><P>(a) Y.</P></DIV8></DIV6>",
        title=1,
        hierarchy={"part": "1", "subpart": "A"},
    )
    files = subpart.export(tmp_path / "s", "pdf", tree=True)
    assert names(files, tmp_path / "s") == ["subpart-A.pdf"]
    assert PdfReader(files[0]).metadata.title == "Subpart A"  # type: ignore[union-attr]


def test_streamed_saves(tmp_path):
    title = load(TITLE_XML, title=45, date=DATE)
    written = []
    files = save_events(
        list(element_events(title.element)),
        title.source,
        tmp_path,
        "epub",
        on_file=written.append,
    )
    assert files == written == [tmp_path / "45.epub"]
    html = save_events(
        element_events(title.element), title.source, tmp_path / "h", "html", tree=True
    )
    assert len(html) == 6
    file = tmp_path / "title.xml"
    file.write_text(TITLE_XML, encoding="utf-8")
    with file.open("rb") as f:
        parts = save_events(
            file_events(f), title.source, tmp_path / "j", "json", tree=True, free=True
        )
    assert len(parts) == 2


def test_render_to_a_string(part):
    assert render(part, "markdown") == part.to_markdown()
    assert render(part, "text", front_matter=False).startswith("PART 164")
    assert render(part, "html").startswith("<!DOCTYPE html>")
    assert json.loads(render(part["164.102"], "json"))["address"] == "45/164.102"


def test_missing_extra(part, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "docx", None)
    with pytest.raises(
        MissingDependencyError, match=r'uv tool install "ecfr-md\[docx\]'
    ):
        part.export(tmp_path / "x.docx")
    assert not (tmp_path / "x.docx").exists()


# Through the client --------------------------------------------------------


def test_client_saves_every_format(ecfr, tmp_path):
    file = ecfr.save("45 CFR Part 164", tmp_path / "hipaa.pdf")
    assert PdfReader(file).outline
    file = ecfr.save("45 CFR Part 164", tmp_path / "hipaa", format="html")
    assert file.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")
    assert ecfr.save("45 CFR Part 164", f"{tmp_path}/d/", format="text").name == (
        "45_part-164.txt"
    )
    pages = ecfr.save("45 CFR Part 164", tmp_path / "site", tree=True, format="html")
    assert len(pages) == 7
    parts = ecfr.save("45 CFR Part 164", tmp_path / "docs", tree=True, format="docx")
    assert names(parts, tmp_path / "docs") == ["part-164.docx"]
    paragraph = ecfr.save("45 CFR 164.104(a)", tmp_path / "a.json")
    assert (
        json.loads(paragraph.read_text(encoding="utf-8"))["address"] == "45/164.104/a"
    )
    with pytest.raises(InvalidParameterError, match="format"):
        ecfr.save("45 CFR Part 164", tmp_path / "x.md", format="rtf")  # type: ignore[arg-type]


def test_client_checks_the_extra_before_downloading(ecfr, api, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "typst", None)
    with pytest.raises(MissingDependencyError):
        ecfr.save("45 CFR Part 164", tmp_path / "x.pdf")
    assert not any("/full/" in p for p in api.paths())


def test_module_save_takes_a_format(ecfr, tmp_path, monkeypatch):
    monkeypatch.setattr("ecfr_md.shortcuts._shared", ecfr)
    file = ecfr_md.save("45 CFR 164.102", tmp_path / "s", format="json")
    assert file.suffix == ".json" or json.loads(file.read_text(encoding="utf-8"))
    files = ecfr_md.save("45 CFR Part 164", tmp_path / "t", tree=True, format="json")
    assert names(files, tmp_path / "t") == ["part-164.json"]


# The command line ------------------------------------------------------------


def test_get_saves_by_suffix_and_format(run, tmp_path, capsys):
    assert run("get", "45 CFR Part 164", "-o", str(tmp_path / "hipaa.pdf")) == 0
    assert PdfReader(tmp_path / "hipaa.pdf").outline
    assert (
        run("get", "45 CFR 164.102", "-o", str(tmp_path / "s"), "--format", "docx") == 0
    )
    assert docx.Document(str(tmp_path / "s")).paragraphs
    assert (
        run(
            "get", "45 CFR 164", "--tree", "-o", str(tmp_path / "t"), "--format", "html"
        )
        == 0
    )
    assert (tmp_path / "t/part-164/subpart-A/164.104.html").is_file()
    assert "Saved" in capsys.readouterr().err


def test_get_prints_other_formats(run, capsys):
    assert run("get", "45 CFR 164.102", "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["citation"] == "45 CFR 164.102"
    assert run("get", "45 CFR 164.102", "--format", "text") == 0
    assert capsys.readouterr().out.startswith("§ 164.102 Statutory basis.\n")
    assert run("get", "45/164.102", "45/164.104", "--format", "text") == 0
    out = capsys.readouterr().out
    assert "§ 164.102" in out and "§ 164.104" in out


@pytest.mark.parametrize(
    ("tokens", "message"),
    [
        (
            ("get", "45 CFR 164.102", "--format", "pdf"),
            "Save PDF to a file: -o FILE.pdf",
        ),
        (("get", "45/164.102", "45/164.104", "--format", "html"), "with -o DIRECTORY/"),
        (("get", "45", "-o", "x.rtf"), "Can't tell what format"),
    ],
)
def test_get_format_usage_errors(run, capsys, tokens, message):
    assert run(*tokens) == 2
    assert message in capsys.readouterr().err


def test_get_without_the_extra(run, capsys, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "typst", None)
    assert run("get", "45 CFR 164.102", "-o", str(tmp_path / "x.pdf")) == 1
    err = capsys.readouterr().err
    assert "Not installed" in err and "ecfr-md[pdf]" in err


def test_get_many_all_and_from_in_a_format(run, tmp_path):
    out = tmp_path / "many"
    assert (
        run("get", "45/164.102", "45/164.104", "-o", f"{out}/", "--format", "json") == 0
    )
    assert sorted(p.name for p in out.iterdir()) == [
        "45_164.102.json",
        "45_164.104.json",
    ]
    tree = tmp_path / "trees"
    assert (
        run(
            "get",
            "45/164.102",
            "45/164.104",
            "--tree",
            "-o",
            str(tree),
            "--format",
            "text",
        )
        == 0
    )
    assert sorted(p.name for p in tree.iterdir()) == ["164.102.txt", "164.104.txt"]
    listing = tmp_path / "list.txt"
    listing.write_text("45 CFR 164.106\n", encoding="utf-8")
    listed = tmp_path / "listed"
    assert (
        run("get", "--from", str(listing), "-o", str(listed), "--format", "epub") == 0
    )
    assert [p.name for p in listed.iterdir()] == ["45_164.106.epub"]
    every = tmp_path / "all"
    assert run("get", "--all", "--tree", "-o", str(every), "--format", "json") == 0
    assert (every / "part-164.json").is_file()
