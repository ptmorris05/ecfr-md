"""See how a section changed between two amendments.

Run: uv run python examples/03_point_in_time.py
"""

import difflib

import ecfr_md

CITATION = "45 CFR 164.502"

# Every change the eCFR has issued to the section, newest first.
changes = ecfr_md.changes(CITATION, since="2017-01-01")
dates = sorted({c.effective for c in changes if c.kind == "substantive"})
print(f"{CITATION} was substantively amended on:", *dates, sep="\n  ")

before = ecfr_md.get(CITATION, date="2017-01-03")
after = ecfr_md.get(CITATION)  # Latest.

diff = difflib.unified_diff(
    before.to_markdown().splitlines(),
    after.to_markdown().splitlines(),
    fromfile=f"{CITATION} on {before.date}",
    tofile=f"{CITATION} on {after.date}",
    lineterm="",
)
print("\n".join(diff))
