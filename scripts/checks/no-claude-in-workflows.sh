#!/usr/bin/env bash
# tool: none
# order: 50
# class: generic
# No workflow or local action references Claude, Anthropic, or a Claude credential (rule WP-15).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

mapfile -t files < <(gate_all_workflow_files)
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
found=0
for f in "${files[@]}"; do
  [[ -f $f ]] || continue
  while IFS=: read -r line _; do
    printf '%s:%s: workflow references Claude or Anthropic\n' "$f" "$line"
    found=1
  done < <(grep -inE 'claude|anthropic' -- "$f" || true)
done
exit "$found"
