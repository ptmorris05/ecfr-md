"""Definitions for a part with no subchapter or chapter above it."""


def test_definitions_stop_at_the_title(ecfr):
    # Part 164 sits under a subtitle here, with no definitions sections.
    assert ecfr.definitions("45 CFR Part 164") == []
