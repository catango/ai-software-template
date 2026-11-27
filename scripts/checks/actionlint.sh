#!/usr/bin/env bash
# tool: actionlint
# order: 90
# class: generic
# actionlint over the workflow files, one line per finding (no source snippet).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

mapfile -t files < <(gate_workflow_files)
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool actionlint

rc=0
out=$(actionlint -no-color -oneline -- "${files[@]}" 2>&1) || rc=$?
if [[ $rc -eq 0 && -z $out ]]; then exit 0; fi
printf '%s\n' "$out" | grep -v '^$' | gate_relative | gate_linter_line
exit 1
