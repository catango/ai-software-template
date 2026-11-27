#!/usr/bin/env bash
# tool: python3
# order: 96
# class: template
# Operator scripts (the paths in `operator_paths` of config/project.yaml) follow the generic operator contract: no shell=True,
# os.system, eval, exec, or dynamic import; a subprocess only with a literal argument list and a program listed in
# `operator_commands`; no token read from the command line or the environment; an --apply flag that is off by default, so the
# dry run is the default. Inactive for an explicit empty `operator_paths`; fails closed when the configuration cannot be read.
# The policy inputs come from config/project.yaml of the BASE commit GATE_BASE_REF (set by quality-gate.sh --base), never from
# the head that is judged; without GATE_BASE_REF the working tree is read (a push, or a local run). Not removable by an override.
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
python3 -I -B "$(dirname "${BASH_SOURCE[0]}")/operator_contract.py" "${args[@]}" || rc=$?
exit "$rc"
