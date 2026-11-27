#!/usr/bin/env bash
# tool: none
# order: 100
# class: generic
# Runs the shell test suites tests/*/run.sh (the gate's own fixture tests)
# that are part of the file list.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

if [[ -n ${GATE_SELFTEST:-} ]]; then exit 77; fi # the tests run the gate; no recursion
files=$(cat)
mapfile -t runners < <(gate_select '^tests/[^/]+/run\.sh$' <<<"$files")
if [[ ${#runners[@]} -eq 0 ]]; then exit 77; fi

found=0
for runner in "${runners[@]}"; do
  rc=0
  out=$(bash "$runner" 2>&1) || rc=$?
  if [[ $rc -eq 0 ]]; then continue; fi
  found=1
  failed=$(grep -E '^FAIL ' <<<"$out" || true)
  if [[ -z $failed ]]; then
    printf '%s:1: test script exited with status %s\n' "$runner" "$rc"
  else
    while IFS= read -r line; do printf '%s:1: test failed: %s\n' "$runner" "${line#FAIL }"; done <<<"$failed"
  fi
done
exit "$found"
