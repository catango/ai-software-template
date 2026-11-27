#!/usr/bin/env bash
# tool: python3
# order: 91
# class: template
# .claude/settings.json contains every entry of .claude/required-deny.txt (the owned generic core list) and of
# .claude/deny.platform.txt (seeded, specific to the platform or project) in permissions.deny. Deleting one entry fails.
# The lists hold one entry per line; blank lines and lines that start with # are ignored.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

files=$(cat)
if ! grep -Eq '^\.claude/(settings\.json|required-deny\.txt|deny\.platform\.txt)$' <<<"$files"; then exit 77; fi
gate_require_tool python3

rc=0
python3 -I -B "$(dirname "${BASH_SOURCE[0]}")/deny_list.py" . || rc=$?
exit "$rc"
