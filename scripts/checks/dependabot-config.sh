#!/usr/bin/env bash
# tool: yq
# order: 45
# class: generic
# Dependabot version updates: .github/dependabot.yml exists, has an update entry for the
# github-actions ecosystem, and no entry sets open-pull-requests-limit to 0 (that would switch the updates off while the file
# looks present). It cannot see that Dependabot does not update container digests or the checksums of the toolchain manifest.
# The file is read from the working tree; the check takes no policy input from the configuration.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

readonly file=.github/dependabot.yml

mapfile -t files
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool yq

if [[ ! -f $file ]]; then
  printf '%s:1: missing; add the file with an update entry for the github-actions ecosystem\n' "$file"
  exit 1
fi

found=0
tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
if ! yq -o=json '.' "$file" >"$tmp" 2>/dev/null; then
  printf '%s:1: not valid YAML\n' "$file"
  exit 1
fi

# `updates` must be a list; an entry counts when its package-ecosystem is exactly github-actions (a comment or another value does not).
count=$(yq '[(.updates // []) | select(tag == "!!seq") | .[] | select(tag == "!!map") | select(.["package-ecosystem"] == "github-actions")] | length' "$file" 2>/dev/null || printf '0')
if [[ $count != [1-9]* ]]; then
  printf '%s:1: no update entry for the github-actions ecosystem\n' "$file"
  found=1
fi

# open-pull-requests-limit: 0 in any entry, also written as "0".
while IFS= read -r line; do
  [[ $line =~ ^[0-9]+$ ]] || continue
  printf '%s:%s: open-pull-requests-limit is 0 (version updates are switched off)\n' "$file" "$line"
  found=1
done < <(yq '(.updates // []) | select(tag == "!!seq") | .[] | select(tag == "!!map") | .["open-pull-requests-limit"] | select(. != null and (. | tostring) == "0") | line' "$file" 2>/dev/null || true)
exit "$found"
