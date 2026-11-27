#!/usr/bin/env bash
# tool: none
# order: 10
# class: generic
# Shell scripts have a bash shebang, `set -euo pipefail` in the first 15 lines, and are executable.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

found=0
applicable=0
while IFS= read -r f; do
  [[ -n $f && -f $f ]] || continue
  gate_is_shell "$f" || continue
  applicable=1
  shebang=$(gate_shebang "$f")
  if [[ ! $shebang =~ ^#!.*[/\ ]bash([[:space:]]|$) ]]; then
    printf '%s:1: shell script without a bash shebang\n' "$f"
    found=1
  fi
  if ! head -n 15 -- "$f" | grep -Eq '^set -E?euo pipefail([[:space:]]*#.*)?[[:space:]]*$'; then
    printf "%s:1: missing 'set -euo pipefail' within the first 15 lines\n" "$f"
    found=1
  fi
  if [[ ! -x $f ]]; then
    printf '%s:1: not executable\n' "$f"
    found=1
  fi
done
if [[ $applicable -eq 0 ]]; then exit 77; fi
exit "$found"
