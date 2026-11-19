"""Exporting CFR text as text, HTML, JSON, EPUB, Word and PDF.

Every format is written from one model: a node becomes blocks (headings,
numbered paragraphs, tables, notes, lists and images; see
:mod:`ecfr_md.render.blocks`), and each writer turns blocks into its format.
Word and PDF need the ``docx`` and ``pdf`` extras.

>>> part.export("hipaa.pdf")             # the format from the suffix
>>> part.export("hipaa/", format="html", tree=True)
"""

from ecfr_md.render.formats import FORMATS, Format, format_for

__all__ = ["FORMATS", "Format", "format_for"]
