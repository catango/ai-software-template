#!/usr/bin/env bash
# tool: pymarkdown
# order: 80
# class: generic
# pymarkdownlnt over every Markdown file, with its front-matter extension on (path-scoped rules in .claude/rules/ start with a
# YAML block; the block is not read as Markdown, the text below it is). Disabled rules (records and PR templates
# violate them by design): md003 heading style, md013 line length, md022 blank lines around
# headings (the templates put a comment directly under a heading), md028, md029, md032 blank lines
# around lists, md033 inline HTML, md034 bare URLs, md038, md041 first-line heading.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

mapfile -t files < <(gate_select '\.(md|markdown)$')
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool pymarkdown

rc=0
out=$(pymarkdown --enable-extensions front-matter --disable-rules md003,md013,md022,md028,md029,md032,md033,md034,md038,md041 scan -- "${files[@]}" 2>&1) || rc=$?
if [[ $rc -eq 0 && -z $out ]]; then exit 0; fi
gate_relative <<<"$out" | gate_linter_line
exit 1
