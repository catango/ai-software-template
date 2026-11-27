#!/usr/bin/env bash
# tool: yq
# order: 40
# class: generic
# Workflow policy, rules WP-1 to WP-16. Text patterns: workflow-policy.awk. Structure (WP-10 to WP-13,
# WP-16, the cache: input of WP-14): workflow_policy.py on `yq -o=json`. The policy inputs allowed_actions and the runner labels (runner_labels, or the runners of architectures)
# come from config/project.yaml of the base commit GATE_BASE_REF (set by quality-gate.sh --base), never from a head that is
# being judged. Without GATE_BASE_REF the working tree is read (the checkout is the base: a push, or a local run).
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

here="$(dirname "${BASH_SOURCE[0]}")"
awk_file="$here/workflow-policy.awk"
py_file="$here/workflow_policy.py"
config=config/project.yaml

mapfile -t files < <(gate_all_workflow_files)
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool yq
gate_require_tool python3

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
found=0

# ---- policy source: the base commit when there is one
src=''
printf 'null\n' >"$tmp/config.json"
if [[ -n ${GATE_BASE_REF:-} ]]; then
  if [[ $GATE_BASE_REF == -* ]] || ! commit=$(git rev-parse --verify --quiet "${GATE_BASE_REF}^{commit}" 2>/dev/null); then
    printf '%s:1: policy: cannot read the base commit; the defaults apply\n' "$config"
    found=1
  elif git cat-file -e "$commit:$config" 2>/dev/null; then
    git show "$commit:$config" >"$tmp/config.yaml"
    src="$tmp/config.yaml"
  fi
elif [[ -f $config ]]; then
  src=$config
fi
if [[ -n $src ]] && ! yq -o=json '.' "$src" >"$tmp/config.json" 2>/dev/null; then
  printf '%s:1: policy: the configuration is not one YAML document; the defaults apply\n' "$config"
  printf 'null\n' >"$tmp/config.json"
  found=1
fi

allowed='' labels=''
rc=0
pol=$(python3 -I -B "$py_file" policy "$tmp/config.json") || rc=$?
if [[ $rc -gt 1 ]]; then exit "$rc"; fi
while IFS= read -r line; do
  case $line in
    'A '*) allowed=${line#A } ;;
    'L '*) labels=${line#L } ;;
    ?*)
      printf '%s\n' "$line"
      found=1
      ;;
  esac
done <<<"$pol"

# ---- line scan, and the parsed form of every file
pairs=()
i=0
for f in "${files[@]}"; do
  [[ -f $f ]] || continue
  out=$(awk -v file="$f" -v allowed="$allowed" -f "$awk_file" "$f")
  if [[ -n $out ]]; then
    printf '%s\n' "$out"
    found=1
  fi
  if [[ $f == .github/workflows/* ]] && ! grep -Eq '^permissions:' -- "$f"; then
    printf '%s:1: WP-1: missing top-level permissions: (use permissions: {})\n' "$f"
    found=1
  fi
  i=$((i + 1))
  if yq -o=json '.' "$f" >"$tmp/$i.json" 2>/dev/null; then
    pairs+=("$f" "$tmp/$i.json")
  else
    printf '%s:1: policy: the file is not valid YAML; the structural rules were not checked\n' "$f"
    found=1
  fi
done

if [[ ${#pairs[@]} -gt 0 ]]; then
  rc=0
  out=$(python3 -I -B "$py_file" scan "$labels" "${pairs[@]}") || rc=$?
  if [[ $rc -gt 1 ]]; then exit "$rc"; fi
  if [[ -n $out ]]; then
    printf '%s\n' "$out"
    found=1
  fi
fi
exit "$found"
