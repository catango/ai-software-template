#!/usr/bin/env bash
# tool: python3
# order: 85
# class: template
# Instruction and documentation files stay within the budgets of .template/budgets.toml (lines, bytes, and the characters of
# a Skill description). A project raises one value with a [[budget]] entry in .template/overrides.toml; the file itself
# cannot be overridden as a whole. Active raises are printed on stderr.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

here="$(dirname "${BASH_SOURCE[0]}")"
files=$(cat)
if ! grep -Eq '^(\.template/(budgets|overrides)\.toml|CLAUDE\.md|docs/|\.claude/(rules|skills)/)' <<<"$files"; then exit 77; fi
gate_require_tool python3

rc=0
python3 -I -B "$here/size_budget.py" . <<<"$files" || rc=$?
exit "$rc"
