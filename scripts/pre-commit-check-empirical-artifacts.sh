#!/usr/bin/env bash
set -euo pipefail
patterns='accountNumber|account_number|clientOrderId|access_token'
raw_files=$(git diff --cached --name-only -- 'scripts/empirical/*.py' 2>/dev/null || true)
[ -z "$raw_files" ] && exit 0
# filter out deleted/non-existent files; handle filenames with spaces
files=()
while IFS= read -r f; do
  [ -f "$f" ] && files+=("$f")
done <<< "$raw_files"
[ ${#files[@]} -eq 0 ] && exit 0
if grep -nE "$patterns" "${files[@]}"; then
  echo "ERROR: empirical scripts must strip broker artifacts before commit" >&2
  exit 1
fi
