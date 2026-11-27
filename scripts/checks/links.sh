#!/usr/bin/env bash
# tool: none
# order: 60
# class: generic
# Relative Markdown links resolve to existing files or directories inside the repository.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

awk_file="$(dirname "${BASH_SOURCE[0]}")/links.awk"
mapfile -t files < <(gate_select '\.(md|markdown)$')
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi

root=$(pwd -P)
found=0
for f in "${files[@]}"; do
  [[ -f $f ]] || continue
  dir=$(dirname "$f")
  while IFS=$'\t' read -r line target; do
    case $target in
      '#'* | *:* | //*) continue ;; # anchor-only, or a URL/mailto scheme
    esac
    target=${target%%#*}
    target=${target%%\?*}
    [[ -n $target ]] || continue
    target=${target//%20/ }
    if [[ $target == /* ]]; then path="$root$target"; else path="$root/$dir/$target"; fi
    resolved=$(realpath -m -- "$path")
    if [[ $resolved != "$root" && $resolved != "$root"/* ]]; then
      printf '%s:%s: relative link escapes the repository\n' "$f" "$line"
      found=1
    elif [[ ! -e $resolved ]]; then
      printf '%s:%s: relative link does not resolve\n' "$f" "$line"
      found=1
    fi
  done < <(awk -f "$awk_file" "$f")
done
exit "$found"
