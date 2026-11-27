#!/usr/bin/env bash
# tool: shellcheck
# order: 20
# class: generic
# ShellCheck over every shell script.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

files=()
while IFS= read -r f; do
  if [[ -n $f && -f $f ]] && gate_is_shell "$f"; then files+=("$f"); fi
done
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool shellcheck

rc=0
out=$(shellcheck -x -f gcc -- "${files[@]}" 2>&1) || rc=$?
if [[ $rc -eq 0 ]]; then exit 0; fi
sed -E 's/^([^:]+):([0-9]+):[0-9]+: [a-z]+: (.*) \[(SC[0-9]+)\]$/\1:\2: \4 \3/' <<<"$out"
exit 1
