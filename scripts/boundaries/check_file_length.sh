#!/usr/bin/env bash
# Fail if any Python file in src/ exceeds 300 lines.
set -euo pipefail

MAX_LINES=300
VIOLATIONS=0
SRC_DIR="$(cd "$(dirname "$0")/../.." && pwd)/src"

while IFS= read -r -d '' file; do
    line_count=$(wc -l < "$file")
    if [ "$line_count" -gt "$MAX_LINES" ]; then
        printf "FAIL: %s has %d lines (limit %d)\n" "$file" "$line_count" "$MAX_LINES"
        VIOLATIONS=$((VIOLATIONS + 1))
    fi
done < <(find "$SRC_DIR" -name "*.py" -print0)

if [ "$VIOLATIONS" -gt 0 ]; then
    printf "check_file_length: %d file(s) exceed the %d-line limit\n" "$VIOLATIONS" "$MAX_LINES"
    exit 1
fi
printf "check_file_length: all files within %d-line limit\n" "$MAX_LINES"
