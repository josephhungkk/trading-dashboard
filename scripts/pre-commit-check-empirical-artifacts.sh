#!/usr/bin/env bash
# Block empirical scripts from committing hardcoded broker credentials/IDs.
# Matches credential VALUES (string literals 16+ chars) NOT bare variable names.
# Variable names like `access_token: str` and SQLite columns are legitimate.
set -euo pipefail

# Match patterns of the form: <key><sep><quote><value 16+ chars><quote>
# where <key> is a sensitive name and <value> is a non-empty string literal.
key_pattern='accountNumber|account_number|clientOrderId|access_token|refresh_token'
value_re="(${key_pattern})[[:space:]]*[=:][[:space:]]*[\"'][A-Za-z0-9_.-]{16,}[\"']"

raw_files=$(git diff --cached --name-only -- 'scripts/empirical/*.py' 2>/dev/null || true)
[ -z "$raw_files" ] && exit 0

files=()
while IFS= read -r f; do
  [ -f "$f" ] && files+=("$f")
done <<< "$raw_files"
[ ${#files[@]} -eq 0 ] && exit 0

if grep -nE "$value_re" "${files[@]}"; then
  echo "ERROR: empirical scripts must not commit hardcoded broker secrets" >&2
  echo "       (matched <key>=<long literal>; use os.environ instead)" >&2
  exit 1
fi
