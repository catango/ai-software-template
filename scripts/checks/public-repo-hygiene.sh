#!/usr/bin/env bash
# tool: none
# order: 30
# class: generic
# scope: all
# No resource identifiers, IPv4 and IPv6 addresses, emails, key fingerprints, private keys, or tokens.
# Findings give the path, line, and kind only; the matched text is never printed.
# One account handle is accepted anywhere it is meant to be public: the single seeded CODEOWNERS line `* @<login>`.
# In a CODEOWNERS file every other line that contains an @ (a second owner, a team, a path rule) is a finding.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

readonly RE_OCID='ocid1[.][a-z0-9_]+[.][a-z0-9-]+[.]'
readonly RE_IPV4='(^|[^0-9.])[0-9]{1,3}([.][0-9]{1,3}){3}(/[0-9]{1,2})?([^0-9]|$)'
# IPv6: eight groups, or a compressed form with at least two groups before the double colon (a MAC address, a time, a ratio,
# a short list, and a scope-resolution path are not addresses). The boundary characters are part of the match and are never printed.
readonly RE_IPV6='(^|[^0-9A-Za-z:])((([0-9A-Fa-f]{1,4}:){7}[0-9A-Fa-f]{1,4})|(([0-9A-Fa-f]{1,4}:){2,6}:[0-9A-Fa-f]{0,4}(:[0-9A-Fa-f]{1,4}){0,5}))([^0-9A-Za-z:]|$)'
readonly RE_EMAIL='[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+([.][A-Za-z0-9-]+)*[.][A-Za-z]{2,}'
readonly RE_FPR_SHA256='SHA256:[A-Za-z0-9+/]{43}'
readonly RE_FPR_MD5='([0-9A-Fa-f]{2}:){15}[0-9A-Fa-f]{2}'
readonly RE_FPR_GPG='([0-9A-F]{4} {1,2}){9}[0-9A-F]{4}'
readonly RE_PRIVATE_KEY='-----BEGIN ([A-Z0-9]+ )*PRIVATE KEY( BLOCK)?-----'
readonly RE_TOKEN='gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}'

# ipv4_allowed TEXT: documentation ranges and the all-zero default route are allowed; invalid octets are not addresses.
ipv4_allowed() {
  local text=$1 mask='' ip a b c d
  ip=${text%%/*}
  if [[ $text == */* ]]; then mask=${text#*/}; fi
  IFS=. read -r a b c d <<<"$ip"
  for o in "$a" "$b" "$c" "$d"; do
    if ((10#$o > 255)); then return 0; fi
  done
  a=$((10#$a)) b=$((10#$b)) c=$((10#$c))
  if [[ $a -eq 0 && $b -eq 0 && $c -eq 0 && $((10#$d)) -eq 0 && $mask == 0 ]]; then return 0; fi
  if [[ $a -eq 192 && $b -eq 0 && $c -eq 2 ]]; then return 0; fi
  if [[ $a -eq 198 && $b -eq 51 && $c -eq 100 ]]; then return 0; fi
  if [[ $a -eq 203 && $b -eq 0 && $c -eq 113 ]]; then return 0; fi
  return 1
}

# ipv6_allowed TEXT: the documentation prefix is allowed, like the documentation IPv4 ranges.
ipv6_allowed() {
  local m=$1
  m=${m#[!0-9A-Fa-f]}
  m=${m%[!0-9A-Fa-f:]}
  m=${m,,}
  [[ $m == 2001:db8:* || $m == 2001:0db8:* ]]
}

found=0
applicable=0

# scan FILE KIND REGEX [allow-kind]: report lines of FILE matching REGEX (the match is never printed).
scan() {
  local f=$1 kind=$2 re=$3 allow=${4:-} line match
  while IFS=: read -r line match; do
    if [[ -n $allow ]] && is_allowed "$allow" "$match"; then continue; fi
    printf '%s:%s: possible %s\n' "$f" "$line" "$kind"
    found=1
  done < <(grep -InoE -- "$re" "$f" 2>/dev/null || true)
}

# is_allowed KIND MATCH: true when the match is an allowed form (the finding is dropped).
is_allowed() {
  local m=$2
  case $1 in
    ipv4)
      m=${m#[!0-9]}
      m=${m%[!0-9]}
      ipv4_allowed "$m"
      ;;
    # only the GitHub noreply form, and a local part that is exactly noreply or no-reply
    email) [[ ${m,,} =~ ^([0-9]+\+)?[a-z0-9-]+@users\.noreply\.github\.com$ || ${m,,} == noreply@* || ${m,,} == no-reply@* ]] ;;
    ipv6) ipv6_allowed "$m" ;;
    *) return 1 ;;
  esac
}

# scan_codeowners FILE: report each line with an @ that is not exactly `* @<login>` (the line is never printed).
scan_codeowners() {
  local f=$1 n=0 line
  while IFS= read -r line || [[ -n $line ]]; do
    n=$((n + 1))
    [[ $line == *@* ]] || continue
    if [[ $line =~ ^\*\ @[A-Za-z0-9]([A-Za-z0-9-]{0,37}[A-Za-z0-9])?$ ]]; then continue; fi
    printf '%s:%s: possible account handle other than the single CODEOWNERS line\n' "$f" "$n"
    found=1
  done <"$f"
}

while IFS= read -r f; do
  [[ -n $f && -f $f ]] || continue
  gate_is_text "$f" || continue
  applicable=1
  if [[ ${f##*/} == CODEOWNERS ]]; then scan_codeowners "$f"; fi
  scan "$f" 'cloud resource identifier' "$RE_OCID"
  scan "$f" 'IPv4 address' "$RE_IPV4" ipv4
  scan "$f" 'IPv6 address' "$RE_IPV6" ipv6
  scan "$f" 'email address' "$RE_EMAIL" email
  scan "$f" 'key fingerprint' "$RE_FPR_SHA256"
  scan "$f" 'key fingerprint' "$RE_FPR_MD5"
  scan "$f" 'key fingerprint' "$RE_FPR_GPG"
  scan "$f" 'private key' "$RE_PRIVATE_KEY"
  scan "$f" 'token' "$RE_TOKEN"
done
if [[ $applicable -eq 0 ]]; then exit 77; fi
exit "$found"
