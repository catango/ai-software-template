#!/usr/bin/env bash
# tool: python3
# order: 90
# class: template
# scope: all
# CLAUDE.md is the owned, platform-neutral instruction file: it carries the instruction-trust rule, the Never list, and the
# precedence sentence, cites no record of another repository, and ends with exactly two imports (docs/PLATFORM.md, then
# docs/PROJECT.md). Neither imported file imports anything. No other CLAUDE.md and no CLAUDE.local.md is tracked. Where
# .template/state.toml exists, CLAUDE.md equals its recorded hash. Sizes are the business of size-budget. The scope is the whole
# tracked tree, tests/*/fixtures/ included: a CLAUDE.md hidden there would still be loaded by Claude Code (the template's own
# fixtures name such files CLAUDE.md.fixture, and the test runner renames them).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

files=$(cat)
if ! grep -Eq '(^|/)CLAUDE(\.local)?\.md$|^docs/(PLATFORM|PROJECT)\.md$|^\.template/state\.toml$' <<<"$files"; then exit 77; fi
gate_require_tool python3

rc=0
python3 -I -B "$(dirname "${BASH_SOURCE[0]}")/claude_md.py" . <<<"$files" || rc=$?
exit "$rc"
