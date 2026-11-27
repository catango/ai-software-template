#!/usr/bin/env bash
# tool: python3
# order: 93
# class: generic
# Static validation of toolchain/ci-manifest.toml and its lock file (toolchain/install check; no network): exact versions,
# an artifact and a SHA-256 for every declared architecture, hash-locked requirements.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

engine="$(dirname "${BASH_SOURCE[0]}")/../../toolchain/install"
files=$(cat)
if ! gate_select '^toolchain/(ci-manifest\.toml|python/[^/]+\.(lock|in))$' <<<"$files" | grep -q .; then exit 77; fi
gate_require_tool python3

rc=0
out=$(python3 -I -B "$engine" --manifest toolchain/ci-manifest.toml --root . check 2>&1) || rc=$?
if [[ $rc -eq 0 ]]; then exit 0; fi
if [[ $rc -eq 2 ]]; then
  printf 'toolchain/ci-manifest.toml:1: manifest could not be checked (usage or file error)\n'
  exit 1
fi
grep -v '^toolchain: ' <<<"$out" | sed -E 's|^|toolchain/ci-manifest.toml:1: |' || true
exit 1
