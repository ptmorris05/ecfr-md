"""See how a section changed between two amendments.

Run: uv run python examples/03_point_in_time.py
"""

import difflib

from ecfr_md import ECFR

CITATION = "45 CFR 164.502"

with ECFR() as ecfr:
    # Every amendment to the section, from the versioner service.
    versions = ecfr.versioner.versions(
        45, part="164", section="164.502", issue_date_gte="2017-01-01"
    ).content_versions
    dates = sorted({v.amendment_date for v in versions if v.substantive})
    print(f"{CITATION} was substantively amended on:", *dates, sep="\n  ")

    before = ecfr.get(CITATION, date="2017-01-03")
    after = ecfr.get(CITATION)  # Latest.

diff = difflib.unified_diff(
    before.to_markdown().splitlines(),
    after.to_markdown().splitlines(),
    fromfile=f"{CITATION} on {before.date}",
    tofile=f"{CITATION} on {after.date}",
    lineterm="",
)
print("\n".join(diff))
