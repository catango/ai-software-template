#!/usr/bin/env bash
# tool: none
# order: 35
# class: template
# .github/CODEOWNERS exists and is exactly the one line `* @<approver_login>` for the approver of config/project.yaml
# The ruleset requires code-owner review, so this one line is what makes the
# approver's review count. Findings name the file and the problem only; no account name is printed.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

readonly file=.github/CODEOWNERS
readonly config=config/project.yaml
readonly login_re='[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?'

mapfile -t files
if [[ ${#files[@]} -eq 0 ]]; then exit 77; fi

# approver_login: the value of the key in the top-level github: block (quotes and a trailing comment removed).
approver=''
if [[ -f $config ]]; then
  approver=$(awk '
    /^[^ \t#]/ { in_github = ($0 ~ /^github:[ \t]*(#.*)?$/); next }
    in_github && $0 ~ /^[ \t]+approver_login:/ {
      v = $0
      sub(/^[ \t]+approver_login:[ \t]*/, "", v)
      if (v ~ /^"/)      { sub(/^"/, "", v); sub(/".*$/, "", v) }
      else if (v ~ /^'\''/) { sub(/^'\''/, "", v); sub(/'\''.*$/, "", v) }
      else               { sub(/[ \t]*(#.*)?$/, "", v) }
      print v
      exit
    }' "$config")
fi
if [[ ! $approver =~ ^${login_re}$ ]]; then
  printf '%s:1: approver_login is missing or is not a valid account name\n' "$config"
  exit 1
fi

if [[ ! -f $file ]]; then
  printf '%s:1: missing; it must be the one line "* @<approver_login>"\n' "$file"
  exit 1
fi

# The file is exactly one line: "* @" and the login, one final newline or none, no CR, no space, no other line.
content=$(cat -- "$file"; printf x)
content=${content%x}
if [[ $content != "* @$approver" && $content != "* @$approver"$'\n' ]]; then
  if [[ $content =~ ^\*\ @${login_re}$'\n'?$ ]]; then
    printf '%s:1: different approver than approver_login of %s\n' "$file" "$config"
  else
    printf '%s:1: must be exactly one line "* @<approver_login>" and nothing else\n' "$file"
  fi
  exit 1
fi
exit 0
