#!/usr/bin/env bash
# tool: python3
# order: 92
# class: template
# An owned file may not change without an entry in .template/overrides.toml (path, reason, pr). "Changed" is measured against the hashes
# of .template/state.toml of the BASE commit GATE_BASE_REF (set by quality-gate.sh --base, or by the workflow), never the head that is
# judged; without GATE_BASE_REF the tree is judged against its own state.toml (a push, or a local run). A removed file is a change too.
# A pull request on a branch template-sync/v<MAJOR>.<MINOR>.<PATCH> that updates state.toml to the branch version together with the files it
# records (and leaves overrides.toml alone), and the first pull request on init/v<MAJOR>.<MINOR>.<PATCH> that adds state.toml, are exempt:
# the branch name comes from GATE_HEAD_REF, else GITHUB_HEAD_REF, and the rules TC-1 and TC-2 of pr-contract judge what such a PR may touch.
# A change limited to uses: lines of the same actions in an owned workflow passes (the reading in scripts/lib/template_sync/uses_drift.py).
# Fails closed when the base commit or a file it reads cannot be read. Not removable by an override. Nothing to judge without state.toml.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

cat >/dev/null # the list of files is not used: the check reads the files that state.toml names
gate_require_tool python3

args=()
if [[ -n ${GATE_BASE_REF:-} ]]; then
  if [[ $GATE_BASE_REF == -* ]] || ! commit=$(git rev-parse --verify --quiet "${GATE_BASE_REF}^{commit}" 2>/dev/null); then
    args+=(--base-unreadable)
  else
    args+=("--base=$commit")
  fi
fi
args+=("--head-ref=${GATE_HEAD_REF:-${GITHUB_HEAD_REF:-}}")
rc=0
python3 -I -B "$(dirname "${BASH_SOURCE[0]}")/owned_drift.py" "${args[@]}" . || rc=$?
exit "$rc"
