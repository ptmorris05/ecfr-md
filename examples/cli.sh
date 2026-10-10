#!/usr/bin/env sh
# Command-line examples. Run: sh examples/cli.sh
set -e
out="$(dirname "$0")/output"

# Print a section; citation words don't need quoting...
ecfr-md get 45 CFR 164.502 | head -5

# ...but parentheses do, because the shell treats them specially.
ecfr-md get "45 CFR 164.502(a)(1)(i)"

# Save a part as one file, or as a directory tree.
ecfr-md get 45 CFR Part 164 -o "$out/45-cfr-164.md"
ecfr-md get 45 --part 164 --tree -o "$out/45-cfr-164"

# Text as of a date.
ecfr-md get 45 CFR 164.502 --date "Jan 1 2018" -o "$out/164.502-2018.md"

# Raw API endpoints print JSON.
ecfr-md versioner latest-date 45
ecfr-md search count "minimum necessary"

# Identify yourself to the API and limit request rate (or use ECFR_MD_* env vars).
ecfr-md --user-agent "my-app/1.0 (me@example.com)" --rate-limit 2 get 45 CFR 160.103 | head -3
