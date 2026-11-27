#!/usr/bin/env bash
# tool: python3
# order: 95
# class: generic
# Python files in scripts/ are syntactically valid; unit tests (tests/*/test_*.py) pass.
# Nothing is written to the repository (no bytecode files).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

input=$(cat)
sources=()
while IFS= read -r f; do
  [[ -n $f && -f $f ]] || continue
  if [[ $f == scripts/* && ($f == *.py || $(gate_shebang "$f") =~ ^#!.*python) ]]; then sources+=("$f"); fi
done <<<"$input"
mapfile -t test_files < <(gate_select '^tests/.*/test_[^/]*\.py$' <<<"$input")
if [[ ${#sources[@]} -eq 0 && ${#test_files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool python3

export PYTHONDONTWRITEBYTECODE=1
found=0
if [[ ${#sources[@]} -gt 0 ]]; then
  if ! out=$(python3 -I -c '
import sys
bad = 0
for path in sys.argv[1:]:
    try:
        with open(path, "rb") as handle:
            compile(handle.read(), path, "exec", dont_inherit=True)
    except SyntaxError as error:
        print(f"{path}:{error.lineno or 1}: syntax error: {error.msg}")
        bad = 1
sys.exit(bad)
' "${sources[@]}"); then
    printf '%s\n' "$out"
    found=1
  fi
fi

mapfile -t test_dirs < <(for f in "${test_files[@]}"; do dirname "$f"; done | sort -u)
for dir in "${test_dirs[@]}"; do
  rc=0
  out=$(python3 -I -B -m unittest discover -s "$dir" -p 'test_*.py' 2>&1) || rc=$?
  if [[ $rc -ne 0 ]]; then
    failed=$(grep -E '^(FAIL|ERROR): ' <<<"$out" | sed -E 's/^(FAIL|ERROR): ([^ ]+).*/\1 \2/' || true)
    if [[ -z $failed ]]; then
      printf '%s/:1: unit tests did not run cleanly (exit %s)\n' "$dir" "$rc"
    else
      while IFS= read -r line; do printf '%s/:1: unit test failed: %s\n' "$dir" "$line"; done <<<"$failed"
    fi
    found=1
  fi
done
exit "$found"
