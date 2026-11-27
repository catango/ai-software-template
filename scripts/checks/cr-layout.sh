#!/usr/bin/env bash
# tool: python3
# order: 97
# class: template
# Change requests keep the layout of docs/cr/_template/: every <root>cr/CR-<n>/ has the five files, the criteria IDs of
# acceptance-criteria.md are unique, every row of the PR table of implementation-plan.md names a criterion ID that is defined,
# and a reference to another repository is qualified (`#<n>`, `<owner>/<repo>#<n>`, and `<repo>:ADR-<n>` are accepted; `<repo>#<n>`
# and `<repo>/ADR-<n>` are not). The template directory is held to the layout too, and so are the ADR files for the references.
# The roots (`workflow.docs_roots`) come from config/project.yaml of the BASE commit GATE_BASE_REF (set by quality-gate.sh
# --base), never from the head that is judged; without GATE_BASE_REF the working tree is read (a push, or a local run).
# Fails closed when the configuration cannot be read. The record directories are read from the tree, not from the file list, so a
# changed-files run still sees the whole change request. Not removable by an override.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

gate_require_tool python3

config=config/project.yaml
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
args=()
if [[ -n ${GATE_BASE_REF:-} ]]; then
  if [[ $GATE_BASE_REF == -* ]] || ! commit=$(git rev-parse --verify --quiet "${GATE_BASE_REF}^{commit}" 2>/dev/null); then
    args=(--config-unreadable)
  elif git cat-file -e "$commit:$config" 2>/dev/null; then
    git show "$commit:$config" >"$tmp/config.yaml"
    args=(--config "$tmp/config.yaml")
  fi
elif [[ -f $config ]]; then
  args=(--config "$config")
fi
rc=0
python3 -I -B "$(dirname "${BASH_SOURCE[0]}")/cr_layout.py" "${args[@]}" . || rc=$?
exit "$rc"
