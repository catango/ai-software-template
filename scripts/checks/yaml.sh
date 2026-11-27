#!/usr/bin/env bash
# tool: yamllint
# order: 70
# class: generic
# yamllint over every YAML file (configuration is inline so the check is one file).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

mapfile -t files < <(gate_select '\.ya?ml$')
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool yamllint

readonly CONFIG='{extends: default, rules: {line-length: disable, document-start: disable, truthy: {check-keys: false}, comments: {min-spaces-from-content: 1}, indentation: {spaces: consistent, indent-sequences: whatever}}}'
out=$(yamllint -d "$CONFIG" -f parsable -- "${files[@]}" 2>&1) || true
if [[ -z $out ]]; then exit 0; fi
gate_relative <<<"$out" | gate_linter_line
exit 1
