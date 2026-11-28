#!/usr/bin/env bash
# Tests for scripts/quality-gate.sh and scripts/checks/*.sh.
# Usage: tests/quality-gate/run.sh [check-id | dispatcher | classes | workflow-policy ...]   (no arguments: everything)
# Fixtures live in tests/quality-gate/fixtures/<check-id>/<pass-*|fail-*>/. A fail case may hold
# an EXPECT file (substring that must appear in the findings). Fixtures that would contain
# forbidden patterns (public-repo-hygiene) are generated here from fragments.
# The catalogue of check classes is tests/quality-gate/catalogue.tsv; the class fixtures are fixtures/_class/<case>/zz-fixture.sh.
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
FIXTURES="$ROOT/tests/quality-gate/fixtures"
CHECKS="$ROOT/scripts/checks"
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

# The tests must not depend on, or modify, the caller's git configuration or identity.
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null
export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=noreply GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=noreply
export GATE_SELFTEST=1
# The policy base commit (and the head branch name that owned-drift reads) is set by the tests that need it, never inherited.
unset GATE_BASE_REF GATE_HEAD_REF GITHUB_HEAD_REF

PASSED=0
FAILED=0
SKIPPED=0
ok() {
  PASSED=$((PASSED + 1))
  printf 'ok   %s\n' "$1"
}
fail() {
  FAILED=$((FAILED + 1))
  printf 'FAIL %s\n' "$1"
  if [[ -n ${2:-} ]]; then printf '     %s\n' "$2"; fi
}
skip() {
  SKIPPED=$((SKIPPED + 1))
  printf 'SKIP %s\n' "$1"
}
# expect NAME CONDITION-STATUS DETAIL: record ok/fail from a command status.
assert() {
  if [[ $2 -eq 0 ]]; then ok "$1"; else fail "$1" "${3:-}"; fi
}

want() {
  local a
  if [[ ${#WANTED[@]} -eq 0 ]]; then return 0; fi
  for a in "${WANTED[@]}"; do
    if [[ $a == "$1" ]]; then return 0; fi
  done
  return 1
}
WANTED=("$@")

header() { awk -v k="$2" 'NR<=12 && $1=="#" && $2==k":" {print $3; exit}' "$CHECKS/$1.sh"; }
has_tool() { [[ -z $1 || $1 == none ]] || command -v "$1" >/dev/null 2>&1; }
rep() { # rep N STRING: STRING repeated N times
  local n=$1 s=''
  while ((n > 0)); do
    s+=$2
    n=$((n - 1))
  done
  printf '%s' "$s"
}
contains() { grep -qF -- "$2" <<<"$1"; }

# ---------------------------------------------------------------- check fixtures

list_files() { (cd "$1" && find . -type f ! -name EXPECT ! -name SECRET | sed 's|^\./||' | LC_ALL=C sort); }

# unfixture DIR: a fixture file named CLAUDE.md.fixture or CLAUDE.local.md.fixture becomes the file without the suffix, so that the
# tracked tree holds no instruction file of that name (the gate's claude-md check sees the whole tree, fixtures included).
unfixture() {
  local f
  while IFS= read -r -d '' f; do mv "$f" "${f%.fixture}"; done < <(find "$1" -type f \( -name 'CLAUDE.md.fixture' -o -name 'CLAUDE.local.md.fixture' \) -print0)
}

run_dir() { # id name dir
  local id=$1 name=$2 dir=$3 tool tmp files out rc expect
  tool=$(header "$id" tool)
  if ! has_tool "$tool"; then
    skip "$id/$name (tool not installed: $tool)"
    return
  fi
  tmp=$(mktemp -d "$WORK/case.XXXXXX")
  cp -R "$dir/." "$tmp/"
  find "$tmp" -type f -name '*.sh' ! -name 'noexec*' -exec chmod 755 {} +
  find "$tmp" -type f -name 'noexec*' -exec chmod 644 {} +
  unfixture "$tmp"
  files=$(list_files "$tmp")
  rc=0
  out=$(cd "$tmp" && "$BASH" "$CHECKS/$id.sh" <<<"$files" 2>&1) || rc=$?
  case $name in
    pass-*)
      if [[ $rc -eq 0 ]]; then ok "$id/$name"; else fail "$id/$name" "expected exit 0, got $rc"; fi
      ;;
    fail-*)
      if [[ $rc -ne 1 ]]; then
        fail "$id/$name" "expected exit 1, got $rc"
        return
      fi
      if grep -qvE '^([^:]+:[0-9]+: .+|raise: .+)$' <<<"$out"; then
        fail "$id/$name" "a finding does not match path:line: message"
        return
      fi
      expect=''
      if [[ -f $dir/EXPECT ]]; then expect=$(<"$dir/EXPECT"); fi
      if [[ -n $expect ]] && ! contains "$out" "$expect"; then
        fail "$id/$name" "findings do not mention: $expect"
        return
      fi
      # An EXPECT of the form "WP-n:" also means that no other rule fires: one failing fixture per rule .
      if [[ $expect =~ ^(WP-[0-9]+):$ ]] && grep -qvF ": ${BASH_REMATCH[1]}: " <<<"$out"; then
        fail "$id/$name" "findings of another rule than ${BASH_REMATCH[1]}: $(grep -vF ": ${BASH_REMATCH[1]}: " <<<"$out" | head -n 1)"
        return
      fi
      if [[ -f $dir/SECRET ]] && contains "$out" "$(<"$dir/SECRET")"; then
        fail "$id/$name" "a finding echoes file content"
        return
      fi
      ok "$id/$name"
      ;;
  esac
}

gen_hygiene_cases() {
  local d="$WORK/gen/public-repo-hygiene" at='@' secret
  mk() { # name expect content [path of the file, default file.txt]
    local rel=${4:-file.txt}
    mkdir -p "$d/$1/$(dirname "$rel")"
    printf '%s\n' "$3" >"$d/$1/$rel"
    printf '%s\n' "$2" >"$d/$1/EXPECT"
    printf '%s\n' "$3" >"$d/$1/SECRET"
  }
  mk fail-cloud-resource-id "identifier" "$(printf 'oc%s%s.%s.%s.%s.%s' id 1 instance oc1 uk-london-1 "$(rep 20 a)")"
  mk fail-private-ipv4 "IPv4" "$(printf '%s.%s.%s.%s' 10 0 0 7)"
  mk fail-public-ipv4 "IPv4" "$(printf '%s.%s.%s.%s' 8 8 8 8)"
  mk fail-email "email" "$(printf '%s%s%s' user "$at" example.com)"
  # an address is allowed only in the GitHub noreply form and as a local part that is exactly noreply or no-reply
  mk fail-email-noreply-inside-the-local-part "email" "$(printf 'person.noreply%sexample.com' "$at")"
  mk fail-email-noreply-prefix-of-the-local-part "email" "$(printf 'noreply.team%sexample.com' "$at")"
  mk fail-email-no-reply-suffix-of-the-local-part "email" "$(printf 'jane-no-reply%sexample.com' "$at")"
  mk fail-email-noreply-github-lookalike-domain "email" "$(printf '1234+bot%susers.noreply.github.com.example.org' "$at")"
  mk fail-email-noreply-github-in-the-local-part "email" "$(printf 'users.noreply.github.com%sexample.org' "$at")"
  # an IPv6 address: eight groups, or a compressed form with at least two groups before the double colon
  mk fail-ipv6-eight-groups "IPv6" "$(printf '%s:%s:%s:%s:%s:%s:%s:%s' 2a02 6b8 0 0 0 0 1 2)"
  mk fail-ipv6-compressed "IPv6" "$(printf 'fd12:3456%s1' '::')"
  mk fail-ipv6-compressed-trailing "IPv6" "$(printf 'fd12:3456:789a%s' '::')"
  mk fail-ipv6-in-a-url "IPv6" "$(printf 'https://[fd12:3456%s5]:8080/x' '::')"
  mk fail-fingerprint-sha256 "fingerprint" "$(printf '%s:%s' SHA256 "$(rep 43 A)")"
  mk fail-fingerprint-md5 "fingerprint" "$(rep 15 'ab:')ab"
  mk fail-private-key "private key" "$(printf -- '-----BEGIN %s-----' 'OPENSSH PRIVATE KEY')"
  mk fail-token-ghp "token" "$(printf '%s_%s' ghp "$(rep 36 x)")"
  mk fail-token-fine-grained "token" "$(printf 'github_pat_%s' "$(rep 30 x)")"
  # the one seeded CODEOWNERS line is accepted, any other owner form is a finding (the handle is never printed).
  mk fail-codeowners-second-owner "account handle" "* @example-approver @other-account" .github/CODEOWNERS
  mk fail-codeowners-team "account handle" "* @example-approver/team" .github/CODEOWNERS
  mk fail-codeowners-path-rule "account handle" "docs/ @example-approver" .github/CODEOWNERS
  mk fail-codeowners-root-file "account handle" "* @example-approver @other-account" CODEOWNERS
  mk fail-codeowners-invalid-login "account handle" "* @-example" .github/CODEOWNERS
  mkdir -p "$d/pass-codeowners-line/.github"
  printf '%s\n' "* @example-approver" >"$d/pass-codeowners-line/.github/CODEOWNERS"
  mkdir -p "$d/pass-codeowners-no-final-newline/.github"
  printf '%s' "* @example-approver" >"$d/pass-codeowners-no-final-newline/.github/CODEOWNERS"
  mkdir -p "$d/pass-handle-outside-codeowners"
  printf '%s\n' "Ask @example-approver in the pull request." >"$d/pass-handle-outside-codeowners/file.txt"
  mkdir -p "$d/pass-noreply-forms"
  printf '%s\n' "$(printf 'noreply%sexample.com' "$at")" "$(printf 'no-reply%sexample.org' "$at")" "$(printf 'NoReply%sexample.org' "$at")" \
    "$(printf '1234+bot%susers.noreply.github.com' "$at")" "$(printf 'bot%susers.noreply.github.com' "$at")" >"$d/pass-noreply-forms/file.txt"
  mkdir -p "$d/pass-ipv6-documentation-prefix"
  printf '%s\n' "$(printf '2001:db8%s1' '::')" "$(printf '%s:%s:%s:%s:%s:%s:%s:%s' 2001 db8 0 0 0 0 0 1)" "$(printf '2001:DB8:abcd%s' '::')" >"$d/pass-ipv6-documentation-prefix/file.txt"
  mkdir -p "$d/pass-not-an-ipv6-address"
  printf '%s\n' "mac $(printf '%s:%s:%s:%s:%s:%s' aa bb cc dd ee ff)" "time $(printf '%s:%s:%s' 12 30 45)" "list $(printf '%s:%s:%s:%s:%s:%s:%s' a b c d e f 0)" \
    "scope $(printf 'ab%scd%sef' '::' '::')" "loopback $(printf '%s1' '::')" "path std$(printf '%s' '::')collections$(printf '%s' '::')HashMap" "ratio $(printf '%s:%s' 16 9)" >"$d/pass-not-an-ipv6-address/file.txt"
  secret=$(printf '%s.%s.%s.%s' 192 0 2 1)
  mkdir -p "$d/pass-doc-range-and-noreply"
  printf '%s\n%s\n%s\n' "$secret" "1234+bot${at}users.noreply.github.com" "$(printf '%s.%s.%s.%s/%s' 0 0 0 0 0)" >"$d/pass-doc-range-and-noreply/file.txt"
}

run_check_fixtures() {
  local id name dir
  gen_hygiene_cases
  gen_claude_md_cases
  gen_cr_layout_cases
  for dir in "$FIXTURES"/*/ "$WORK"/gen/*/; do
    [[ -d $dir ]] || continue
    id=$(basename "$dir")
    [[ $id == _* ]] && continue # _class holds the class fixtures, run by test_classes
    want "$id" || continue
    if [[ ! -f $CHECKS/$id.sh ]]; then
      fail "$id" "fixtures exist but scripts/checks/$id.sh does not"
      continue
    fi
    for c in "$dir"*/; do
      name=$(basename "$c")
      run_dir "$id" "$name" "${c%/}"
    done
  done
}

run_notool() { # a check whose tool is missing must exit 78 (SKIP), not fake a result
  local id=$1 tool tmp bin files rc
  tool=$(header "$id" tool)
  [[ -n $tool && $tool != none ]] || return 0
  bin=$(restricted_bin)
  tmp=$(mktemp -d "$WORK/notool.XXXXXX")
  cp -R "$FIXTURES/$id/pass-"*/. "$tmp/" 2>/dev/null || cp -R "$FIXTURES/$id/"*/. "$tmp/"
  files=$(list_files "$tmp")
  rc=0
  (cd "$tmp" && PATH="$bin" "$BASH" "$CHECKS/$id.sh" <<<"$files" >/dev/null 2>&1) || rc=$?
  if [[ $rc -eq 78 ]]; then ok "$id: exits 78 without $tool"; else fail "$id: exits 78 without $tool" "got $rc"; fi
}

restricted_bin() { # a PATH with the basics but none of the linters
  local d="$WORK/bin" t p
  if [[ -d $d ]]; then
    printf '%s' "$d"
    return
  fi
  mkdir -p "$d"
  for t in git awk sed grep sort cat head tail tr cut wc uniq find xargs mktemp rm cp chmod mkdir dirname basename realpath env ls date; do
    if p=$(command -v "$t" 2>/dev/null); then ln -sf "$p" "$d/$t"; fi
  done
  printf '%s' "$d"
}

run_adapters() { # normalisation of linter output, tested with stand-in tools (no verdicts are faked)
  local id=$1 tool=$2 raw=$3 want_line=$4 file=$5 tmp bin out rc
  bin="$WORK/stub-$id"
  mkdir -p "$bin"
  cp -P "$(restricted_bin)"/* "$bin/"
  # shellcheck disable=SC2016  # the stand-in tool must expand these variables itself
  printf '#!/bin/sh\n[ -z "$STUB_OUTPUT" ] || printf "%%s\\n" "$STUB_OUTPUT"\nexit "$STUB_RC"\n' >"$bin/$tool"
  chmod +x "$bin/$tool"
  tmp=$(mktemp -d "$WORK/adapter.XXXXXX")
  printf 'x\n' >"$tmp/$file"
  rc=0
  out=$(cd "$tmp" && STUB_OUTPUT="$raw" STUB_RC=1 PATH="$bin" "$BASH" "$CHECKS/$id.sh" <<<"$file" 2>&1) || rc=$?
  if [[ $rc -eq 1 ]] && contains "$out" "$want_line"; then ok "$id: adapter normalises $tool output"; else fail "$id: adapter normalises $tool output" "rc=$rc"; fi
  rc=0
  out=$(cd "$tmp" && STUB_OUTPUT="$tmp/$raw" STUB_RC=1 PATH="$bin" "$BASH" "$CHECKS/$id.sh" <<<"$file" 2>&1) || rc=$?
  if [[ $rc -eq 1 ]] && contains "$out" "$want_line" && ! contains "$out" "$tmp"; then ok "$id: adapter makes absolute paths relative"; else fail "$id: adapter makes absolute paths relative" "rc=$rc"; fi
  rc=0
  out=$(cd "$tmp" && STUB_OUTPUT="" STUB_RC=0 PATH="$bin" "$BASH" "$CHECKS/$id.sh" <<<"$file" 2>&1) || rc=$?
  if [[ $rc -eq 0 && -z $out ]]; then ok "$id: adapter passes clean $tool run"; else fail "$id: adapter passes clean $tool run" "rc=$rc"; fi
}

# ---------------------------------------------------------------- dispatcher

new_repo() {
  local d
  d=$(mktemp -d "$WORK/repo.XXXXXX")
  git -C "$d" init -q -b main
  cp -R "$ROOT/scripts" "$d/scripts"
  # operator-contract fails closed without a readable configuration; this one has no operator paths
  mkdir -p "$d/config"
  printf 'schema_version: 1\nworkflow:\n  operator_paths: []\n' >"$d/config/project.yaml"
  printf '%s\n' "$d"
}
commit_all() {
  git -C "$1" add -A
  git -C "$1" commit -q -m "${2:-test}"
}
good_script() { printf '#!/usr/bin/env bash\nset -euo pipefail\necho ok\n' >"$1/$2" && chmod 755 "$1/$2"; }
bad_script() { printf '#!/usr/bin/env bash\necho no strict mode\n' >"$1/$2" && chmod 755 "$1/$2"; }
gate() { # repo args...: sets OUT and RC
  local repo=$1
  shift
  RC=0
  OUT=$("$BASH" "$repo/scripts/quality-gate.sh" "$@" 2>&1) || RC=$?
}
# rc_is NAME EXPECTED: compares RC from the last gate call
rc_is() { if [[ $RC -eq $2 ]]; then ok "$1"; else fail "$1" "expected exit $2, got $RC"; fi; }
out_has() { if contains "$OUT" "$2"; then ok "$1"; else fail "$1" "output lacks: $2"; fi; }
out_lacks() { if contains "$OUT" "$2"; then fail "$1" "output unexpectedly has: $2"; else ok "$1"; fi; }

test_dispatcher() {
  local repo bin i n expected

  # exit 0 and 1, summary line
  repo=$(new_repo)
  good_script "$repo" good.sh
  commit_all "$repo"
  gate "$repo" --only bash-hygiene
  rc_is "exit 0 when all selected checks pass" 0
  out_has "PASS line" "PASS bash-hygiene"
  out_has "summary line" "quality-gate: 0 failed, 1 passed, 0 skipped"
  bad_script "$repo" bad.sh
  commit_all "$repo"
  gate "$repo" --only bash-hygiene
  rc_is "exit 1 when a check fails" 1
  out_has "FAIL line with count" "FAIL bash-hygiene (1)"
  out_has "finding is path:line: message" "  bad.sh:1: "
  out_has "summary counts the failure" "quality-gate: 1 failed, 0 passed, 0 skipped"

  # exit 2
  gate "$repo" --no-such-option
  rc_is "exit 2 on unknown option" 2
  gate "$repo" --only no-such-check
  rc_is "exit 2 on unknown check id" 2
  gate "$repo" --base
  rc_is "exit 2 on --base without value" 2
  gate "$repo" --base no-such-ref --only bash-hygiene
  rc_is "exit 2 on unresolvable --base" 2
  gate "$repo" stray-argument
  rc_is "exit 2 on a positional argument" 2

  # --list, --only, --skip, --base
  gate "$repo" --list
  rc_is "--list exits 0" 0
  expected="bash-hygiene shellcheck public-repo-hygiene workflow-policy forbidden-api no-claude-in-workflows links yaml markdown size-budget actionlint claude-md deny-list owned-drift toolchain python operator-contract cr-layout gate-tests"
  n=$(awk '{print $1}' <<<"$OUT" | grep -Ev '^(ID|CHECK)$' | tr '\n' ' ' | sed 's/ $//')
  if [[ $n == "$expected" ]]; then ok "--list shows check ids in order"; else fail "--list shows check ids in order" "got: $n"; fi
  out_has "--list shows the tool" "shellcheck"
  gate "$repo" --only bash-hygiene,links
  out_has "--only runs the selected checks" "bash-hygiene"
  out_has "--only runs the selected checks (second id)" "links"
  out_lacks "--only leaves the others out" "shellcheck"
  gate "$repo" --only bash-hygiene,links --skip links
  out_has "--skip keeps the rest" "bash-hygiene"
  out_lacks "--skip removes the check" " links"
  good_script "$repo" newer.sh
  commit_all "$repo"
  gate "$repo" --only bash-hygiene --base HEAD~1
  rc_is "--base limits to changed files (bad file unchanged since base)" 0
  bad_script "$repo" newest-bad.sh
  commit_all "$repo"
  gate "$repo" --only bash-hygiene --base HEAD~1
  rc_is "--base sees files added since the base" 1
  out_lacks "--base does not report unchanged files" "  bad.sh:"

  # fixtures are test inputs, not repository content
  repo=$(new_repo)
  mkdir -p "$repo/tests/demo/fixtures"
  bad_script "$repo/tests/demo/fixtures" bad.sh
  good_script "$repo" good.sh
  commit_all "$repo"
  gate "$repo" --only bash-hygiene
  rc_is "files under tests/*/fixtures/ are not checked" 0

  # ... except by a check that declares "# scope: all": the identifier scan reads every tracked file, fixtures included
  repo=$(new_repo)
  mkdir -p "$repo/tests/demo/fixtures"
  printf '%s_%s\n' ghp "$(rep 36 x)" >"$repo/tests/demo/fixtures/token.txt"
  good_script "$repo" good.sh
  commit_all "$repo"
  gate "$repo" --only public-repo-hygiene
  rc_is "a token in a project's fixture is found by public-repo-hygiene" 1
  out_has "the finding names the fixture file" "tests/demo/fixtures/token.txt:1: possible token"
  gate "$repo" --only bash-hygiene
  rc_is "the other checks still skip the fixtures" 0
  gate "$repo" --only public-repo-hygiene --base HEAD
  rc_is "--base without a change in the fixture leaves it alone" 0

  # missing tool: SKIP locally, exit 3 under --strict (a failure takes priority)
  bin=$(restricted_bin)
  RC=0
  OUT=$(PATH="$bin" "$BASH" "$repo/scripts/quality-gate.sh" --only shellcheck 2>&1) || RC=$?
  rc_is "missing tool is not an error without --strict" 0
  out_has "SKIP names the tool" "SKIP shellcheck (tool missing: shellcheck)"
  RC=0
  OUT=$(PATH="$bin" "$BASH" "$repo/scripts/quality-gate.sh" --strict --only shellcheck 2>&1) || RC=$?
  rc_is "missing tool is exit 3 with --strict" 3
  bad_script "$repo" bad.sh
  commit_all "$repo"
  RC=0
  OUT=$(PATH="$bin" "$BASH" "$repo/scripts/quality-gate.sh" --strict --only shellcheck,bash-hygiene 2>&1) || RC=$?
  rc_is "a failing check outranks a missing tool (exit 1)" 1
  gate "$repo" --only links
  out_has "no applicable files is SKIP (no files)" "SKIP links (no files)"
  rc_is "no applicable files is not an error" 0
  gate "$repo" --strict --only links
  rc_is "no applicable files is allowed under --strict" 0

  # at most 20 findings per check plus a count; --verbose shows all
  repo=$(new_repo)
  for i in $(seq 1 30); do bad_script "$repo" "bad$i.sh"; done
  commit_all "$repo"
  gate "$repo" --only bash-hygiene
  n=$(grep -cE '^  bad[0-9]+\.sh:' <<<"$OUT" || true)
  if [[ $n -eq 20 ]]; then ok "shows 20 of 30 findings"; else fail "shows 20 of 30 findings" "got $n"; fi
  out_has "states how many are hidden" "... 10 more (use --verbose)"
  out_has "FAIL line carries the total" "FAIL bash-hygiene (30)"
  n=$(grep -c '^quality-gate:' <<<"$OUT" || true)
  if [[ $n -eq 1 ]]; then ok "exactly one summary line"; else fail "exactly one summary line" "got $n"; fi
  gate "$repo" --only bash-hygiene --verbose
  n=$(grep -cE '^  bad[0-9]+\.sh:' <<<"$OUT" || true)
  if [[ $n -eq 30 ]]; then ok "--verbose shows all findings"; else fail "--verbose shows all findings" "got $n"; fi
  out_lacks "--verbose has no truncation note" "more (use --verbose)"

  # writes nothing to the repository; needs no network, credentials, or environment
  repo=$(new_repo)
  good_script "$repo" good.sh
  printf '# T\n\n[a](good.sh)\n' >"$repo/README.md"
  mkdir -p "$repo/.github/workflows"
  printf 'name: x\non: push\npermissions: {}\njobs:\n  quality-gate:\n    runs-on: ubuntu-latest\n    timeout-minutes: 5\n    steps:\n      - run: echo hi\n' >"$repo/.github/workflows/ci.yml"
  commit_all "$repo"
  RC=0
  OUT=$(cd "$repo" && env -i PATH="$PATH" HOME="$WORK/no-home" "$BASH" scripts/quality-gate.sh 2>&1) || RC=$?
  n=$(git -C "$repo" status --porcelain | wc -l)
  if [[ $n -eq 0 ]]; then ok "repository is clean after a run"; else fail "repository is clean after a run" "$n paths changed"; fi
  if [[ $RC -eq 0 ]]; then ok "runs with an empty environment (no credentials)"; else fail "runs with an empty environment (no credentials)" "exit $RC: $OUT"; fi
  if command -v unshare >/dev/null 2>&1 && unshare -rn true 2>/dev/null; then
    RC=0
    OUT=$(unshare -rn "$BASH" "$repo/scripts/quality-gate.sh" 2>&1) || RC=$?
    if [[ $RC -eq 0 ]]; then ok "runs without network"; else fail "runs without network" "exit $RC"; fi
  else
    skip "runs without network (unshare -rn unavailable; static scan below)"
  fi
  if grep -rEn '(^|[^A-Za-z_-])(curl|wget|ssh|scp|nc)[[:space:]]|git[[:space:]]+(fetch|pull|push|clone)' \
    "$ROOT/scripts" --include='*.sh' --exclude='workflow-policy.sh' | grep -v ':[[:space:]]*#' >"$WORK/net.txt"; then
    fail "no network commands in scripts/" "$(head -n 3 "$WORK/net.txt")"
  else
    ok "no network commands in scripts/"
  fi

  # step summary
  RC=0
  OUT=$(GITHUB_STEP_SUMMARY="$WORK/summary.md" "$BASH" "$repo/scripts/quality-gate.sh" --only bash-hygiene,links 2>&1) || RC=$?
  if [[ -f $WORK/summary.md ]] && grep -q '^| Check | Result |' "$WORK/summary.md" && grep -q '^| bash-hygiene | PASS |' "$WORK/summary.md"; then
    ok "summary table appended to GITHUB_STEP_SUMMARY"
  else
    fail "summary table appended to GITHUB_STEP_SUMMARY"
  fi
  bad_script "$repo" bad.sh
  commit_all "$repo"
  GITHUB_STEP_SUMMARY="$WORK/summary.md" "$BASH" "$repo/scripts/quality-gate.sh" --only bash-hygiene >/dev/null 2>&1 || true
  if grep -q '^| bash-hygiene | FAIL |' "$WORK/summary.md" && grep -q 'bad.sh:1:' "$WORK/summary.md"; then
    ok "summary appends (does not overwrite) and lists findings"
  else
    fail "summary appends (does not overwrite) and lists findings"
  fi

  # robustness: a crashing check is a failure; output cannot forge workflow commands
  repo=$(new_repo)
  printf '#!/usr/bin/env bash\n# tool: none\n# order: 999\n# class: project\nset -euo pipefail\nexit 5\n' >"$repo/scripts/checks/zz-crash.sh"
  chmod 755 "$repo/scripts/checks/zz-crash.sh"
  commit_all "$repo"
  gate "$repo" --only zz-crash
  rc_is "a crashing check fails the gate" 1
  out_has "a crashing check is reported" "crashed (exit 5)"
  repo=$(new_repo)
  printf '#!/usr/bin/env bash\n# tool: none\n# order: 999\n# class: project\n# scope: weird\nset -euo pipefail\nexit 0\n' >"$repo/scripts/checks/zz-scope.sh"
  chmod 755 "$repo/scripts/checks/zz-scope.sh"
  commit_all "$repo"
  gate "$repo" --list
  rc_is "an unknown scope header fails the gate" 1
  out_has "an unknown scope is named" 'zz-scope: unknown scope "weird"'
  repo=$(new_repo)
  bad_script "$repo" '::notice::x.sh'
  commit_all "$repo"
  gate "$repo" --only bash-hygiene
  if grep -q '^::' <<<"$OUT" || contains "$OUT" '::notice::'; then
    fail "workflow-command sequences in findings are neutralised"
  else
    ok "workflow-command sequences in findings are neutralised"
  fi
}

# ---------------------------------------------------------------- check classes 

CATALOGUE="$ROOT/tests/quality-gate/catalogue.tsv"
CLASS_RE='^(generic|template|platform|project)$'

# "id class" of the catalogue rows with status present, and of the last gate --list (in $OUT).
catalogue_present() { awk -F'\t' '$0 !~ /^#/ && $3 == "present" {print $1 " " $2}' "$CATALOGUE" | LC_ALL=C sort; }
list_pairs() { awk 'NF >= 3 {print $1 " " $3}' <<<"$OUT" | LC_ALL=C sort; }
catalogue_diff() { diff <(catalogue_present) <(list_pairs); }

test_classes() {
  local repo dir name id class status n d

  # the catalogue file itself
  n=$(grep -vc '^#' "$CATALOGUE" || true)
  if [[ $n -gt 0 ]]; then ok "the catalogue has rows"; else fail "the catalogue has rows"; fi
  while IFS=$'\t' read -r id class status; do
    if [[ $class =~ $CLASS_RE ]]; then ok "catalogue class of $id"; else fail "catalogue class of $id" "got: $class"; fi
    case $status in
      present) [[ -f $CHECKS/$id.sh ]] && d=0 || d=1 ;;
      planned) [[ ! -f $CHECKS/$id.sh ]] && d=0 || d=1 ;;
      *) d=1 ;;
    esac
    assert "catalogue status of $id ($status) matches scripts/checks/" "$d" "status: $status"
  done < <(grep -v '^#' "$CATALOGUE")
  n=$(grep -v '^#' "$CATALOGUE" | cut -f1 | sort | uniq -d | tr '\n' ' ')
  if [[ -z $n ]]; then ok "catalogue ids are unique"; else fail "catalogue ids are unique" "duplicates: $n"; fi

  # this repository: every check has a class, and --list equals the catalogue
  for f in "$CHECKS"/*.sh; do
    id=$(basename "$f" .sh)
    class=$(header "$id" class)
    if [[ $class =~ $CLASS_RE ]]; then ok "$id has a class header (first 12 lines)"; else fail "$id has a class header (first 12 lines)" "got: '$class'"; fi
  done
  gate "$ROOT" --list
  rc_is "--list exits 0 with every class valid" 0
  if d=$(catalogue_diff); then ok "--list equals the catalogue (ids and classes)"; else fail "--list equals the catalogue (ids and classes)" "$(tr '\n' ' ' <<<"$d")"; fi

  # fixtures: a check with a missing or unknown class fails the gate, also under --list
  for dir in "$FIXTURES"/_class/*/; do
    name=$(basename "$dir")
    repo=$(new_repo)
    cp "$dir/zz-fixture.sh" "$repo/scripts/checks/zz-fixture.sh"
    commit_all "$repo"
    case $name in
      pass-*)
        gate "$repo" --list
        rc_is "$name: --list exits 0" 0
        out_has "$name: --list prints the class" "project"
        gate "$repo" --only zz-fixture
        rc_is "$name: the check runs" 0
        ;;
      fail-missing-class | fail-unknown-class)
        want_msg='has no "# class:" header'
        [[ $name == fail-unknown-class ]] && want_msg='unknown class "experimental"'
        gate "$repo" --list
        rc_is "$name: --list fails" 1
        out_has "$name: --list names the check and the problem" "$(<"$dir/EXPECT"): $want_msg"
        gate "$repo" --only bash-hygiene
        rc_is "$name: the gate fails even when the check is not selected" 1
        out_has "$name: the gate names the check and the problem" "$(<"$dir/EXPECT"): $want_msg"
        out_lacks "$name: nothing is run after the failure" "PASS"
        ;;
      fail-not-in-catalogue)
        gate "$repo" --list
        rc_is "$name: --list itself succeeds" 0
        if d=$(catalogue_diff); then fail "$name: --list differs from the catalogue"; else
          if contains "$d" "$(<"$dir/EXPECT")"; then ok "$name: --list differs from the catalogue"; else fail "$name: the difference names the check" "$d"; fi
        fi
        ;;
    esac
  done

  # a changed class is a difference; a class header below line 12 does not count
  repo=$(new_repo)
  sed -i 's/^# class: generic$/# class: platform/' "$repo/scripts/checks/bash-hygiene.sh"
  gate "$repo" --list
  if catalogue_diff >/dev/null; then fail "a changed class differs from the catalogue"; else ok "a changed class differs from the catalogue"; fi
  repo=$(new_repo)
  sed -i '/^# class: /d' "$repo/scripts/checks/bash-hygiene.sh"
  { head -n 3 "$repo/scripts/checks/bash-hygiene.sh"; for n in $(seq 1 10); do printf '# filler\n'; done; printf '# class: generic\n'; tail -n +4 "$repo/scripts/checks/bash-hygiene.sh"; } >"$repo/b.sh"
  mv "$repo/b.sh" "$repo/scripts/checks/bash-hygiene.sh"
  gate "$repo" --list
  rc_is "a class header after line 12 counts as missing" 1
  out_has "a class header after line 12 is reported" 'bash-hygiene: has no "# class:" header'
}

# ---------------------------------------------------------------- workflow-policy

WP_FX="$FIXTURES/workflow-policy"

# policy_repo CONFIG_FILE_OR_EMPTY: a repository with the passing workflow; commits it as the base commit and prints its path.
policy_repo() {
  local repo
  repo=$(new_repo)
  mkdir -p "$repo/.github/workflows" "$repo/config"
  cp "$WP_FX/pass-good/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  if [[ -n $1 ]]; then cp "$1" "$repo/config/project.yaml"; else printf 'schema_version: 1\n' >"$repo/config/project.yaml"; fi
  commit_all "$repo" base
  printf '%s\n' "$repo"
}
# head_commit REPO: commit the working tree as the head and print the base commit (the parent).
head_commit() {
  commit_all "$1" head
  git -C "$1" rev-parse HEAD~1
}

test_workflow_policy() {
  local n d repo base cfg_widen

  # each rule WP-1 to WP-16 has a failing fixture; there are passing fixtures. WP-15 is the check no-claude-in-workflows.
  for n in $(seq 1 16); do
    if [[ $n -eq 15 ]]; then
      d=$(find "$FIXTURES/no-claude-in-workflows" -maxdepth 1 -name 'fail-*' | wc -l)
    else
      d=$(grep -lx "WP-$n:" "$WP_FX"/fail-*/EXPECT 2>/dev/null | wc -l)
    fi
    if [[ $d -ge 1 ]]; then ok "WP-$n has a failing fixture"; else fail "WP-$n has a failing fixture"; fi
  done
  d=$(find "$WP_FX" -maxdepth 1 -name 'pass-*' | wc -l)
  if [[ $d -ge 1 ]]; then ok "there is a passing fixture"; else fail "there is a passing fixture"; fi

  command -v yq >/dev/null 2>&1 || {
    skip "workflow-policy base-commit tests (tool not installed: yq)"
    return
  }

  # allowed_actions and runner_labels come from the base commit, not from the head. The head widens the policy
  # and uses the widening in the same change: without a base it passes (working-tree policy), with a base it fails.
  repo=$(policy_repo "")
  mkdir -p "$repo/.github/actions/local"
  cp "$WP_FX/fail-wp4-action-outside-allowlist/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  cp "$WP_FX/pass-allowlist-in-config/config/project.yaml" "$repo/config/project.yaml"
  base=$(head_commit "$repo")
  gate "$repo" --only workflow-policy
  rc_is "a head that widens allowed_actions passes when read from the working tree" 0
  gate "$repo" --only workflow-policy --base "$base"
  rc_is "--base: the same head fails by the base allowed_actions" 1
  out_has "--base: the finding is WP-4" "WP-4:"
  RC=0
  OUT=$(GATE_BASE_REF=$base "$BASH" "$repo/scripts/quality-gate.sh" --only workflow-policy 2>&1) || RC=$?
  rc_is "GATE_BASE_REF: the same head fails by the base allowed_actions" 1
  RC=0
  OUT=$(GATE_BASE_REF=no-such-ref "$BASH" "$repo/scripts/quality-gate.sh" --only workflow-policy 2>&1) || RC=$?
  rc_is "an unreadable base commit fails closed" 1
  out_has "an unreadable base commit is named" "policy: cannot read the base commit"
  RC=0
  OUT=$(GATE_BASE_REF=--no-such-option "$BASH" "$repo/scripts/quality-gate.sh" --only workflow-policy 2>&1) || RC=$?
  rc_is "a base value that looks like an option fails closed" 1

  # the base allows the action: the head passes under --base
  repo=$(policy_repo "$WP_FX/pass-allowlist-in-config/config/project.yaml")
  cp "$WP_FX/pass-allowlist-in-config/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  base=$(head_commit "$repo")
  gate "$repo" --only workflow-policy --base "$base"
  rc_is "an action allowed by the base commit passes" 0

  # runner_labels: widened in the head, used in the head
  repo=$(policy_repo "")
  cp "$WP_FX/pass-runner-labels-in-config/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  cp "$WP_FX/pass-runner-labels-in-config/config/project.yaml" "$repo/config/project.yaml"
  base=$(head_commit "$repo")
  gate "$repo" --only workflow-policy
  rc_is "a head that widens runner_labels passes when read from the working tree" 0
  gate "$repo" --only workflow-policy --base "$base"
  rc_is "--base: the same head fails by the base runner_labels" 1
  out_has "--base: the finding is WP-13" "WP-13:"

  # a base commit with an invalid key fails closed; a base without a configuration file means the defaults
  cfg_widen=$(mktemp "$WORK/cfg.XXXXXX")
  printf 'schema_version: 1\nallowed_actions: actions/checkout\n' >"$cfg_widen"
  repo=$(policy_repo "$cfg_widen")
  printf '# touch\n' >>"$repo/.github/workflows/ci.yml"
  base=$(head_commit "$repo")
  gate "$repo" --only workflow-policy --base "$base"
  rc_is "an invalid key in the base configuration fails closed" 1
  out_has "the invalid key is named" "allowed_actions"
  repo=$(policy_repo "")
  git -C "$repo" rm -q config/project.yaml
  commit_all "$repo" "no config"
  printf '# touch\n' >>"$repo/.github/workflows/ci.yml"
  base=$(head_commit "$repo")
  gate "$repo" --only workflow-policy --base "$base"
  rc_is "a base without a configuration file means the defaults (this workflow passes)" 0

  # a job of ci.yml that the aggregate quality-gate does not need fails, and the finding names the job
  repo=$(policy_repo "")
  cp "$WP_FX/fail-wp16-aggregate-misses-a-job/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  commit_all "$repo" head
  gate "$repo" --only workflow-policy
  rc_is "a job missing from the needs of the aggregate fails" 1
  out_has "the missing job is named" "the job extra-job of ci.yml is not in the needs of the aggregate quality-gate"
  out_lacks "the job that is needed is not reported" "the job gate of ci.yml"
  cp "$WP_FX/pass-aggregate/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  commit_all "$repo" head
  gate "$repo" --only workflow-policy
  rc_is "an aggregate that needs every other job passes" 0
  cp "$WP_FX/fail-wp16-aggregate-no-always/.github/workflows/ci.yml" "$repo/.github/workflows/ci.yml"
  commit_all "$repo" head
  gate "$repo" --only workflow-policy
  rc_is "an aggregate without if: always() fails" 1
  out_has "the missing if: always() is reported" "the aggregate quality-gate has no if: always()"
}

# ---------------------------------------------------------------- claude-md (cases that need run-time values)

# Fixtures that need a record number or a hash are built here: the numbers come from fragments, and the hash is computed.
gen_claude_md_cases() {
  local d="$WORK/gen/claude-md" src="$FIXTURES/claude-md/pass-ok" name cr='CR' adr='ADR'
  mk() { # name: a copy of the passing fixture
    mkdir -p "$d/$1"
    cp -R "$src/." "$d/$1/"
    unfixture "$d/$1"
  }
  mk fail-cites-a-change-request
  printf '\nSee %s-%s for the reason.\n' "$cr" 12 >>"$d/fail-cites-a-change-request/docs/PROJECT.md"
  sed -i "s|^- Merge a pull request.|- Merge a pull request, as $cr-12 says.|" "$d/fail-cites-a-change-request/CLAUDE.md"
  printf '%s\n' "CLAUDE.md:11: cites a change request or decision record" >"$d/fail-cites-a-change-request/EXPECT"
  mk fail-cites-a-decision-record
  sed -i "s|^- Merge a pull request.|- Merge a pull request, as $adr-003 says.|" "$d/fail-cites-a-decision-record/CLAUDE.md"
  printf '%s\n' "cites a change request or decision record" >"$d/fail-cites-a-decision-record/EXPECT"
  mk pass-email-address-is-not-an-import
  sed -i "s|^- Merge a pull request.|- Merge a pull request. Write to someone$(printf '@')example.org.|" "$d/pass-email-address-is-not-an-import/CLAUDE.md"
  mk pass-record-number-in-the-seeded-platform-file
  printf '\nPlatform records: %s-%s.\n' "$adr" 11 >>"$d/pass-record-number-in-the-seeded-platform-file/docs/PLATFORM.md"
  mk pass-state-hash-matches
  mkdir -p "$d/pass-state-hash-matches/.template"
  printf 'schema_version = 1\n\n[owned]\n"CLAUDE.md" = "%s"\n' "$(sha256sum "$src/CLAUDE.md.fixture" | cut -d' ' -f1)" >"$d/pass-state-hash-matches/.template/state.toml"
  mk fail-edited-after-the-hash-was-recorded
  mkdir -p "$d/fail-edited-after-the-hash-was-recorded/.template"
  printf 'schema_version = 1\n\n[owned]\n"CLAUDE.md" = "%s"\n' "$(sha256sum "$src/CLAUDE.md.fixture" | cut -d' ' -f1)" >"$d/fail-edited-after-the-hash-was-recorded/.template/state.toml"
  printf '\n- Never mind.\n' >>"$d/fail-edited-after-the-hash-was-recorded/CLAUDE.md"
  printf '%s\n' "differs from its hash" >"$d/fail-edited-after-the-hash-was-recorded/EXPECT"
}

# claude-md sees the whole tracked tree, the inputs of tests (tests/*/fixtures/) included: Claude Code loads a CLAUDE.md that sits
# next to any file it reads, so one hidden there would still be an instruction file.
test_claude_md_scope() {
  local repo
  repo=$(new_repo)
  cp -R "$FIXTURES/claude-md/pass-ok/." "$repo/"
  unfixture "$repo"
  commit_all "$repo" base
  gate "$repo" --only claude-md
  rc_is "claude-md: a tree that follows the rules passes" 0
  mkdir -p "$repo/tests/unit/fixtures/case"
  printf '# hidden\n' >"$repo/tests/unit/fixtures/case/CLAUDE.md"
  commit_all "$repo" nested
  gate "$repo" --only claude-md
  rc_is "claude-md: a CLAUDE.md under tests/*/fixtures/ is found" 1
  out_has "claude-md: the finding names the path" "tests/unit/fixtures/case/CLAUDE.md:1"
  rm "$repo/tests/unit/fixtures/case/CLAUDE.md"
  printf 'x\n' >"$repo/tests/unit/fixtures/case/CLAUDE.local.md"
  commit_all "$repo" local
  gate "$repo" --only claude-md
  rc_is "claude-md: a CLAUDE.local.md under tests/*/fixtures/ is found" 1
}

# ---------------------------------------------------------------- operator-contract (policy from the base commit)

OC_FX="$FIXTURES/operator-contract"

test_operator_contract() {
  local repo base
  # operator_paths and operator_commands come from the base commit, not from the head. The head widens the allowlist, or
  # empties operator_paths, and relies on it in the same change: from the working tree it passes, from the base it fails.
  repo=$(new_repo)
  mkdir -p "$repo/config" "$repo/ops"
  cp "$OC_FX/pass-ok/config/project.yaml" "$repo/config/project.yaml"
  cp "$OC_FX/pass-ok/ops/tool.py" "$repo/ops/tool.py"
  commit_all "$repo" base
  cp "$OC_FX/fail-subprocess-program-not-listed/ops/tool.py" "$repo/ops/tool.py"
  sed -i 's|    - "git"|    - "git"\n    - "curl"|' "$repo/config/project.yaml"
  base=$(head_commit "$repo")
  gate "$repo" --only operator-contract
  rc_is "operator-contract: a head that widens operator_commands passes when read from the working tree" 0
  gate "$repo" --only operator-contract --base "$base"
  rc_is "operator-contract --base: the same head fails by the base operator_commands" 1
  out_has "operator-contract --base: the finding is the program" "does not list"
  RC=0
  OUT=$(GATE_BASE_REF=$base "$BASH" "$repo/scripts/quality-gate.sh" --only operator-contract 2>&1) || RC=$?
  rc_is "operator-contract GATE_BASE_REF: the same head fails by the base operator_commands" 1

  repo=$(new_repo)
  mkdir -p "$repo/config" "$repo/ops"
  cp "$OC_FX/pass-ok/config/project.yaml" "$repo/config/project.yaml"
  cp "$OC_FX/fail-os-system/ops/tool.py" "$repo/ops/tool.py"
  commit_all "$repo" base
  cp "$OC_FX/pass-inactive-when-operator-paths-is-empty/config/project.yaml" "$repo/config/project.yaml"
  printf '# touch\n' >>"$repo/ops/tool.py"
  base=$(head_commit "$repo")
  gate "$repo" --only operator-contract
  rc_is "operator-contract: a head that empties operator_paths passes when read from the working tree" 0
  gate "$repo" --only operator-contract --base "$base"
  rc_is "operator-contract --base: the same head fails by the base operator_paths" 1
  out_has "operator-contract --base: the finding is os.system" "os.system"

  # the base allows the program: the head passes under --base
  repo=$(new_repo)
  mkdir -p "$repo/config" "$repo/ops"
  cp "$OC_FX/pass-ok/config/project.yaml" "$repo/config/project.yaml"
  cp "$OC_FX/pass-ok/ops/tool.py" "$repo/ops/tool.py"
  printf '\n# touch\n' >>"$repo/ops/tool.py"
  commit_all "$repo" base
  printf '# touch\n' >>"$repo/ops/tool.py"
  base=$(head_commit "$repo")
  gate "$repo" --only operator-contract --base "$base"
  rc_is "operator-contract --base: a script that follows the base policy passes" 0

  # an unreadable base fails closed; so does a base without a configuration file
  RC=0
  OUT=$(GATE_BASE_REF=no-such-ref "$BASH" "$repo/scripts/quality-gate.sh" --only operator-contract 2>&1) || RC=$?
  rc_is "operator-contract: an unreadable base commit fails closed" 1
  RC=0
  OUT=$(GATE_BASE_REF=--no-such-option "$BASH" "$repo/scripts/quality-gate.sh" --only operator-contract 2>&1) || RC=$?
  rc_is "operator-contract: a base value that looks like an option fails closed" 1
  git -C "$repo" rm -q config/project.yaml
  commit_all "$repo" "no config"
  printf '# touch\n' >>"$repo/ops/tool.py"
  base=$(head_commit "$repo")
  gate "$repo" --only operator-contract --base "$base"
  rc_is "operator-contract: a base without a configuration file fails closed" 1
  out_has "operator-contract: the failure says so" "fails closed"
}

# ---------------------------------------------------------------- operator_guard

# make_tag REPO NAME [annotated]: a tag written as a ref file (the tests make no `git tag` call, which the gate forbids).
make_tag() {
  local repo=$1 name=$2 sha obj
  sha=$(git -C "$repo" rev-parse HEAD)
  if [[ ${3:-} == annotated ]]; then
    obj=$(printf 'object %s\ntype commit\ntag %s\ntagger test <noreply> 1700000000 +0000\n\nrelease\n' "$sha" "$name" | git -C "$repo" hash-object -t tag -w --stdin)
    sha=$obj
  fi
  mkdir -p "$repo/.git/refs/tags"
  printf '%s\n' "$sha" >"$repo/.git/refs/tags/$name"
}
guard_repo() { # a throwaway repository with one commit, and origin/main (with origin/HEAD) at it
  local d
  d=$(mktemp -d "$WORK/guard.XXXXXX")
  git -C "$d" init -q -b main
  printf 'one\n' >"$d/file"
  commit_all "$d" one
  git -C "$d" update-ref refs/remotes/origin/main HEAD
  git -C "$d" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  printf '%s\n' "$d"
}
guard() { # repo args...: sets OUT and RC
  local repo=$1
  shift
  RC=0
  OUT=$(python3 -I -B "$ROOT/scripts/lib/operator_guard.py" --repo "$repo" "$@" 2>&1) || RC=$?
}

test_operator_guard() {
  local repo side first
  command -v git >/dev/null 2>&1 || {
    skip "operator_guard tests (tool not installed: git)"
    return
  }
  repo=$(guard_repo)
  make_tag "$repo" v1.2.3
  guard "$repo"
  rc_is "operator_guard: a clean checkout at a release tag in the default branch is allowed" 0
  out_has "operator_guard: it prints the tag" "v1.2.3"

  repo=$(guard_repo)
  make_tag "$repo" v2.0.0 annotated
  guard "$repo"
  rc_is "operator_guard: an annotated release tag is allowed" 0

  repo=$(guard_repo)
  guard "$repo"
  rc_is "operator_guard: an untagged commit is refused" 1
  out_has "operator_guard: the refusal says HEAD is not a release tag" "HEAD is not a release tag"

  repo=$(guard_repo)
  make_tag "$repo" v1.0.0
  printf 'two\n' >"$repo/file"
  commit_all "$repo" two
  git -C "$repo" update-ref refs/remotes/origin/main HEAD
  guard "$repo"
  rc_is "operator_guard: a commit after the tag is refused" 1

  repo=$(guard_repo)
  make_tag "$repo" release-1
  make_tag "$repo" v1.2
  make_tag "$repo" v1.2.3-rc1
  make_tag "$repo" v01.2.3
  guard "$repo"
  rc_is "operator_guard: tags that are not release versions are refused" 1

  # a release tag on a commit that the default branch does not contain
  repo=$(guard_repo)
  git -C "$repo" switch -q -c side
  printf 'side\n' >"$repo/file"
  commit_all "$repo" side
  make_tag "$repo" v3.0.0
  guard "$repo"
  rc_is "operator_guard: a tag not contained in the default branch is refused" 1
  out_has "operator_guard: the refusal says so" "not contained in the default branch"
  git -C "$repo" update-ref refs/remotes/origin/main HEAD
  guard "$repo"
  rc_is "operator_guard: the same tag is allowed once the default branch contains it" 0

  repo=$(guard_repo)
  make_tag "$repo" v1.0.0
  printf 'dirty\n' >"$repo/file"
  guard "$repo"
  rc_is "operator_guard: a modified work tree is refused" 1
  git -C "$repo" checkout -q -- file
  printf 'x\n' >"$repo/untracked"
  guard "$repo"
  rc_is "operator_guard: an untracked file is refused" 1

  repo=$(guard_repo)
  make_tag "$repo" v1.0.0
  git -C "$repo" symbolic-ref --delete refs/remotes/origin/HEAD
  guard "$repo"
  rc_is "operator_guard: an unknown default branch is refused" 1
  out_has "operator_guard: the refusal says the default branch is unknown" "default branch is not known"
  guard "$repo" --default-branch main
  rc_is "operator_guard: a default branch that is passed is used" 0
  guard "$repo" --default-branch no-such-branch
  rc_is "operator_guard: a default branch that does not exist is refused" 1
  guard "$repo" --default-branch '--x'
  rc_is "operator_guard: a default branch that looks like an option is refused" 1

  guard "$WORK"
  rc_is "operator_guard: a directory that is not a work tree is refused" 1
  guard "-x"
  rc_is "operator_guard: a repository path that looks like an option is refused" 1

  # --verify-remote: the tag must also exist on the remote, at the same commit (a throwaway bare repository, no network)
  repo=$(guard_repo)
  make_tag "$repo" v1.0.0
  git clone -q --bare "$repo" "$WORK/remote-with-tag.git"
  guard "$repo" --verify-remote "$WORK/remote-with-tag.git"
  rc_is "operator_guard --verify-remote: a tag that the remote has at HEAD is allowed" 0
  repo=$(guard_repo)
  git clone -q --bare "$repo" "$WORK/remote-without-tag.git"
  make_tag "$repo" v1.0.0
  guard "$repo"
  rc_is "operator_guard: without --verify-remote a tag that exists only locally is allowed (the documented limit)" 0
  guard "$repo" --verify-remote "$WORK/remote-without-tag.git"
  rc_is "operator_guard --verify-remote: a tag that only exists locally is refused" 1
  out_has "operator_guard --verify-remote: the refusal says the remote lacks it" "not found on the remote"
  repo=$(guard_repo)
  first=$(git -C "$repo" rev-parse HEAD)
  printf 'two\n' >"$repo/file"
  commit_all "$repo" two
  git -C "$repo" update-ref refs/remotes/origin/main HEAD
  git clone -q --bare "$repo" "$WORK/remote-moved-tag.git"
  mkdir -p "$WORK/remote-moved-tag.git/refs/tags"
  printf '%s\n' "$first" >"$WORK/remote-moved-tag.git/refs/tags/v1.0.0"
  make_tag "$repo" v1.0.0
  guard "$repo" --verify-remote "$WORK/remote-moved-tag.git"
  rc_is "operator_guard --verify-remote: a release tag that the remote has on another commit is refused" 1
  repo=$(guard_repo)
  make_tag "$repo" v2.0.0 annotated
  git clone -q --bare "$repo" "$WORK/remote-annotated.git"
  guard "$repo" --verify-remote "$WORK/remote-annotated.git"
  rc_is "operator_guard --verify-remote: an annotated tag is compared by the commit it points at" 0
  guard "$repo" --verify-remote "$WORK/no-such-remote.git"
  rc_is "operator_guard --verify-remote: a remote that cannot be read is refused" 1
  guard "$repo" --verify-remote 'ext::true'
  rc_is "operator_guard --verify-remote: a remote helper transport is refused" 1
  guard "$repo" --verify-remote '--x'
  rc_is "operator_guard --verify-remote: a remote that looks like an option is refused" 1
  RC=0
  OUT=$(cd "$repo" && python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import operator_guard as g; print(g.require_release(True, verify_remote=sys.argv[2]))' "$ROOT/scripts/lib" "$WORK/remote-annotated.git" 2>&1) || RC=$?
  rc_is "operator_guard.require_release: verify_remote is a parameter" 0

  # the library entry: a dry run is always allowed; an apply run is checked
  repo=$(guard_repo)
  RC=0
  OUT=$(cd "$repo" && python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import operator_guard as g; g.require_release(False)' "$ROOT/scripts/lib" 2>&1) || RC=$?
  rc_is "operator_guard.require_release: a dry run is allowed from an untagged commit" 0
  RC=0
  OUT=$(cd "$repo" && python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import operator_guard as g; g.require_release(True)' "$ROOT/scripts/lib" 2>&1) || RC=$?
  rc_is "operator_guard.require_release: an apply run from an untagged commit exits 1" 1
  out_has "operator_guard.require_release: the message names the guard" "operator_guard: refused"
  make_tag "$repo" v1.0.0
  RC=0
  OUT=$(cd "$repo" && python3 -I -B -c 'import sys; sys.path.insert(0, sys.argv[1]); import operator_guard as g; print(g.require_release(True))' "$ROOT/scripts/lib" 2>&1) || RC=$?
  rc_is "operator_guard.require_release: an apply run from a release tag returns" 0
  side=$(git -C "$repo" for-each-ref --format='%(refname)' refs/tags/)
  if [[ $side == refs/tags/v1.0.0 ]]; then ok "operator_guard: it creates no tag"; else fail "operator_guard: it creates no tag" "$side"; fi
}

# ---------------------------------------------------------------- instruction files (T-4, T-5, T-6 on the files of this tree)

# frontmatter_paths FILE: prints the globs of the `paths:` list of the frontmatter; fails when the frontmatter is malformed.
frontmatter_paths() {
  awk '
    NR == 1 { if ($0 != "---") exit 2; next }
    $0 == "---" { closed = 1; exit }
    /^paths:[ ]*$/ { inlist = 1; next }
    inlist && /^  - "[^"]+"[ ]*$/ { sub(/^  - "/, ""); sub(/"[ ]*$/, ""); print; found++; next }
    { exit 3 }
    END { if (!closed || !found) exit 4 }
  ' "$1"
}

test_instruction_files() {
  local f name globs want_paths
  # each rule file has valid path scoping: public-repo.md loads always (no frontmatter); the others carry a `paths:` list
  for name in public-repo bash workflows docs multi-arch operator; do
    f="$ROOT/.claude/rules/$name.md"
    if [[ ! -f $f ]]; then
      fail "rule $name.md exists"
      continue
    fi
    ok "rule $name.md exists"
    if [[ $name == public-repo ]]; then
      if [[ $(head -n 1 "$f") != '---' ]]; then ok "rule public-repo.md has no path scoping (always loaded)"; else fail "rule public-repo.md has no path scoping (always loaded)"; fi
      continue
    fi
    if globs=$(frontmatter_paths "$f") && [[ -n $globs ]]; then ok "rule $name.md has a valid paths: list"; else fail "rule $name.md has a valid paths: list"; fi
  done
  want_paths=$(printf '%s\n' '.github/workflows/**' | head -n 1)
  if grep -qxF "$want_paths" <(frontmatter_paths "$ROOT/.claude/rules/workflows.md" || true); then ok "workflows.md is scoped to the workflow directory"; else fail "workflows.md is scoped to the workflow directory"; fi
  if frontmatter_paths "$ROOT/.claude/rules/bash.md" | grep -qxF '**/*.sh'; then ok "bash.md is scoped to shell files"; else fail "bash.md is scoped to shell files"; fi
  # the rules that state the operator contract name each part of it
  for want in 'Never run one' 'Dry run is the default' 'Release tags only' 'Credentials are prompted for or short-lived' \
    'Fact collection is read-only, enforced in code' 'Output is safe to paste' 'No infrastructure change'; do
    if grep -qF "$want" "$ROOT/.claude/rules/operator.md"; then ok "operator.md states: $want"; else fail "operator.md states: $want"; fi
  done
  # the guardrails of settings.json
  if python3 -I -B - "$ROOT/.claude/settings.json" <<'PY'; then ok "settings.json keeps bypass mode off and project MCP servers disabled"; else fail "settings.json keeps bypass mode off and project MCP servers disabled"; fi
import json, sys
s = json.load(open(sys.argv[1]))
sys.exit(0 if s["permissions"]["disableBypassPermissionsMode"] == "disable" and s["enableAllProjectMcpServers"] is False else 1)
PY
  # every GraphQL mutation that forbidden-api rejects in scripts is denied for Claude Code's own `gh api` commands too (a mutation
  # name is a write by its nature, so a pattern on it cannot catch a read); the two files are the same set
  if python3 -I -B - "$ROOT" <<'PY'; then ok "required-deny.txt denies every mutation name of forbidden-api.toml"; else fail "required-deny.txt denies every mutation name of forbidden-api.toml"; fi
import json, sys, tomllib
root = sys.argv[1]
data = tomllib.load(open(root + "/scripts/checks/forbidden-api.toml", "rb"))
names = [n for entry in data["graphql"] for n in entry["names"]]
lines = [l.strip() for l in open(root + "/.claude/required-deny.txt") if l.strip() and not l.startswith("#")]
deny = set(json.load(open(root + "/.claude/settings.json"))["permissions"]["deny"])
missing = [n for n in names if f"Bash(gh api *{n}*)" not in lines]
sys.exit(1 if missing or set(lines) - deny else 0)
PY
  # the core list is the generic part: it names no infrastructure tool, cloud, host, or architecture
  # (the words are written in pieces so that the payload scan does not find them in this file)
  if grep -Eiq "terraform|tofu|\\bo""ci\\b|oracle""cloud|amd""64|arm""64|aarch""64|x86" "$ROOT/.claude/required-deny.txt"; then
    fail "required-deny.txt names nothing specific to a platform"
  else
    ok "required-deny.txt names nothing specific to a platform"
  fi
}

# ---------------------------------------------------------------- cr-layout and new-cr (T-7)

# The change requests of these fixtures are made by the real scripts/new-cr from the real skeleton, and are then changed one way at
# a time. Record numbers are written from fragments: the payload scan finds no record number in this file.
CR='CR'
ADR='ADR'
CRP='CR-'
ADP='ADR-'
CRL_CONFIG=$'schema_version: 1\nworkflow:\n  docs_roots:\n    - "docs/"\n  operator_paths: []\n'
TICK=$(printf '\140')
FENCE="$TICK$TICK$TICK"
CR_FILES=(README.md impact-analysis.md design.md acceptance-criteria.md implementation-plan.md)

# cr_stage DIR [N]: a minimal repository (scripts, configuration, the skeleton) in which scripts/new-cr has made change request N.
cr_stage() {
  local d=$1 n=${2:-7}
  mkdir -p "$d/config" "$d/docs/cr"
  cp -R "$ROOT/scripts" "$d/scripts"
  cp -R "$ROOT/docs/cr/_template" "$d/docs/cr/_template"
  printf '%s' "$CRL_CONFIG" >"$d/config/project.yaml"
  (cd "$d" && python3 -I -B scripts/new-cr "$n" demo >/dev/null)
}

gen_cr_layout_cases() {
  local d="$WORK/gen/cr-layout" stage="$WORK/stage-cr" cr7 f
  cr7="docs/cr/${CRP}7"
  if [[ ! -d $stage ]]; then cr_stage "$stage"; fi
  mk() { # mk NAME [EXPECT]: the generated change request, the configuration, and the skeleton
    mkdir -p "$d/$1/docs/cr" "$d/$1/config"
    cp -R "$stage/docs/cr/_template" "$d/$1/docs/cr/_template"
    cp -R "$stage/$cr7" "$d/$1/$cr7"
    cp "$stage/config/project.yaml" "$d/$1/config/project.yaml"
    if [[ -n ${2:-} ]]; then printf '%s\n' "$2" >"$d/$1/EXPECT"; fi
  }
  mk pass-new-cr-skeleton
  mkdir -p "$d/pass-empty-tree/config"
  printf '%s' "$CRL_CONFIG" >"$d/pass-empty-tree/config/project.yaml"
  mkdir -p "$d/pass-skeleton-only/docs/cr" "$d/pass-skeleton-only/config"
  cp -R "$stage/docs/cr/_template" "$d/pass-skeleton-only/docs/cr/_template"
  printf '%s' "$CRL_CONFIG" >"$d/pass-skeleton-only/config/project.yaml"
  mkdir -p "$d/pass-no-docs-directory/config"
  printf 'schema_version: 1\n' >"$d/pass-no-docs-directory/config/project.yaml"
  mk pass-qualified-references
  {
    printf '\nSee #12, other-owner/other-repo#34, and other-repo:%s-5. A record of this repository: %s-6.\n' "$ADR" "$ADR"
    printf 'A link [x](../other-repo#9) and https://example.org/other-repo#9 are not read.\n'
    printf '%s\nother-repo#9 other-repo/%s-9\n%s\n' "$FENCE" "$ADR" "$FENCE"
  } >>"$d/pass-qualified-references/$cr7/design.md"
  mk pass-second-root
  mkdir -p "$d/pass-second-root/records/cr"
  mv "$d/pass-second-root/$cr7" "$d/pass-second-root/records/cr/${CRP}7"
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "docs/"\n    - "records/"\n' >"$d/pass-second-root/config/project.yaml"
  mk pass-criteria-with-formatting
  sed -i "s/^| AC-1 |/| **${TICK}AC-1${TICK}** |/" "$d/pass-criteria-with-formatting/$cr7/acceptance-criteria.md"
  sed -i "s/| AC-1 |\$/| ${TICK}AC-1${TICK} and AC-1 |/" "$d/pass-criteria-with-formatting/$cr7/implementation-plan.md"

  for f in "${CR_FILES[@]}"; do
    mk "fail-missing-$f" "$f:1: the change request has no readable regular file"
    rm "$d/fail-missing-$f/$cr7/$f"
  done
  mk fail-file-is-a-symbolic-link "design.md:1: the change request has no readable regular file"
  rm "$d/fail-file-is-a-symbolic-link/$cr7/design.md"
  ln -s README.md "$d/fail-file-is-a-symbolic-link/$cr7/design.md"
  mk fail-skeleton-lacks-a-file "_template/design.md:1: the change request has no readable regular file"
  rm "$d/fail-skeleton-lacks-a-file/docs/cr/_template/design.md"
  mk fail-duplicate-criterion-id "duplicate criterion ID AC-1 (first at line"
  printf '| AC-1 | a second criterion with the same ID | evidence |\n' >>"$d/fail-duplicate-criterion-id/$cr7/acceptance-criteria.md"
  mk fail-criterion-without-an-id "is not of the form AC-1"
  printf '| criterion two | a criterion without an ID | evidence |\n' >>"$d/fail-criterion-without-an-id/$cr7/acceptance-criteria.md"
  mk fail-criteria-table-has-no-rows "no table with the columns ID"
  sed -i '/^| AC-1 |/d' "$d/fail-criteria-table-has-no-rows/$cr7/acceptance-criteria.md"
  mk fail-no-criteria-table "no table with the columns ID"
  sed -i '/^|/d' "$d/fail-no-criteria-table/$cr7/acceptance-criteria.md"
  mk fail-plan-row-without-criterion-id "plan row 2 names no criterion ID"
  sed -i '/^| 1 | /a | 2 | second | content | needs | |' "$d/fail-plan-row-without-criterion-id/$cr7/implementation-plan.md"
  mk fail-plan-row-names-an-undefined-criterion "names AC-9, which acceptance-criteria.md does not define"
  sed -i 's/| AC-1 |$/| AC-1, AC-9 |/' "$d/fail-plan-row-names-an-undefined-criterion/$cr7/implementation-plan.md"
  mk fail-plan-without-criteria-column "no PR table with an Acceptance criteria column"
  sed -i 's/ | Acceptance criteria |$/ | Covers |/' "$d/fail-plan-without-criteria-column/$cr7/implementation-plan.md"
  mk fail-plan-id-in-another-column "plan row 1 names no criterion ID"
  sed -i 's/^| 1 | .*/| 1 | demo | adds AC-1 | AC-1 | none |/' "$d/fail-plan-id-in-another-column/$cr7/implementation-plan.md"
  mk fail-unqualified-repository-reference "unqualified reference to another repository \"other-repo#12\""
  printf '\nBlocked by other-repo#12.\n' >>"$d/fail-unqualified-repository-reference/$cr7/impact-analysis.md"
  mk fail-unqualified-reference-in-a-table-cell "README.md:"
  sed -i 's/^| ADR | none |/| ADR | other-repo#3 |/' "$d/fail-unqualified-reference-in-a-table-cell/$cr7/README.md"
  mk fail-unqualified-adr-path "write other-repo:${ADP}3"
  printf '\nSee other-repo/%s-3.\n' "$ADR" >>"$d/fail-unqualified-adr-path/$cr7/design.md"
  mk fail-unqualified-change-request-path "write other-repo:${CRP}3"
  printf '\nSee other-repo/%s-3.\n' "$CR" >>"$d/fail-unqualified-change-request-path/$cr7/design.md"
  mk fail-unqualified-reference-in-an-adr "docs/adr/${ADP}1-x.md:"
  mkdir -p "$d/fail-unqualified-reference-in-an-adr/docs/adr"
  printf '# %s-1: x\n\nSupersedes other-repo#4.\n' "$ADR" >"$d/fail-unqualified-reference-in-an-adr/docs/adr/${ADP}1-x.md"
  mk fail-unqualified-reference-in-an-extra-file "notes.md:"
  printf 'Waiting for other-repo#4.\n' >"$d/fail-unqualified-reference-in-an-extra-file/$cr7/notes.md"
  # The cap on Markdown files (200 per change request directory) fails closed: exactly 200 files pass, 201 fail with a finding.
  mk pass-exactly-200-markdown-files
  for f in $(seq 1 195); do printf 'extra %s\n' "$f" >"$d/pass-exactly-200-markdown-files/$cr7/extra-$f.md"; done
  mk fail-more-than-200-markdown-files "more than 200 Markdown files; not all were checked"
  for f in $(seq 1 196); do printf 'extra %s\n' "$f" >"$d/fail-more-than-200-markdown-files/$cr7/extra-$f.md"; done
  mk fail-unqualified-reference-after-the-cap "more than 200 Markdown files; not all were checked"
  for f in $(seq 1 250); do printf 'extra %s\n' "$f" >"$d/fail-unqualified-reference-after-the-cap/$cr7/extra-$f.md"; done
  printf 'Blocked by other-repo#12.\n' >"$d/fail-unqualified-reference-after-the-cap/$cr7/zz-last.md"
  mk fail-not-a-change-request-directory "not a change request directory"
  mkdir -p "$d/fail-not-a-change-request-directory/docs/cr/notes"
  printf 'x\n' >"$d/fail-not-a-change-request-directory/docs/cr/notes/README.md"
  mk fail-change-request-number-with-a-leading-zero "not a change request directory"
  mv "$d/fail-change-request-number-with-a-leading-zero/$cr7" "$d/fail-change-request-number-with-a-leading-zero/docs/cr/${CRP}07"
  mk fail-change-request-without-the-prefix "not a change request directory"
  mv "$d/fail-change-request-without-the-prefix/$cr7" "$d/fail-change-request-without-the-prefix/docs/cr/7"
  mk fail-second-root-is-read "records/cr/${CRP}8/design.md:1"
  mkdir -p "$d/fail-second-root-is-read/records/cr/${CRP}8"
  printf '# x\n' >"$d/fail-second-root-is-read/records/cr/${CRP}8/README.md"
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "docs/"\n    - "records/"\n' >"$d/fail-second-root-is-read/config/project.yaml"
  mk fail-configuration-is-missing "fails closed"
  rm "$d/fail-configuration-is-missing/config/project.yaml"
  mk fail-docs-roots-malformed "docs_roots cannot be read"
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - docs\n' >"$d/fail-docs-roots-malformed/config/project.yaml"
  mk fail-docs-roots-misspelled "docs_roots cannot be read"
  printf 'schema_version: 1\nworkflow:\n  docs_root:\n    - "docs/"\n' >"$d/fail-docs-roots-misspelled/config/project.yaml"
  mk fail-cr-directory-is-a-symbolic-link "is a symbolic link"
  mv "$d/fail-cr-directory-is-a-symbolic-link/docs/cr/_template" "$d/fail-cr-directory-is-a-symbolic-link/elsewhere"
  rm -r "$d/fail-cr-directory-is-a-symbolic-link/docs/cr"
  ln -s ../elsewhere "$d/fail-cr-directory-is-a-symbolic-link/docs/cr"
}

# cr-layout reads the roots from the base commit, like the other checks: a head that drops a root from docs_roots does not
# take its change requests out of the check.
test_cr_layout_policy() {
  local repo="$WORK/cr-policy" base cr7="docs/cr/${CRP}7"
  cr_stage "$repo"
  git -C "$repo" init -q -b main
  printf '# the change request is broken\n' >"$repo/$cr7/design.md"
  rm "$repo/$cr7/impact-analysis.md"
  commit_all "$repo" base
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "elsewhere/"\n' >"$repo/config/project.yaml"
  base=$(head_commit "$repo")
  gate "$repo" --only cr-layout
  rc_is "cr-layout: a head that drops docs/ from docs_roots passes when read from the working tree" 0
  gate "$repo" --only cr-layout --base "$base"
  rc_is "cr-layout --base: the same head fails by the base docs_roots" 1
  out_has "cr-layout --base: the finding names the missing file" "impact-analysis.md"
  RC=0
  OUT=$(GATE_BASE_REF=$base "$BASH" "$repo/scripts/quality-gate.sh" --only cr-layout 2>&1) || RC=$?
  rc_is "cr-layout GATE_BASE_REF: the same head fails by the base docs_roots" 1
  # a changed-files run still sees the whole change request: only design.md changed since the base
  git -C "$repo" checkout -q "$base" -- config/project.yaml
  printf 'one more line\n' >>"$repo/$cr7/design.md"
  git -C "$repo" commit -q -am head2
  gate "$repo" --only cr-layout --base "$base"
  rc_is "cr-layout --base: a change to one file is judged with the files that did not change" 1
  out_has "cr-layout --base: the file that did not change is found missing" "impact-analysis.md"
  RC=0
  OUT=$(GATE_BASE_REF=no-such-ref "$BASH" "$repo/scripts/quality-gate.sh" --only cr-layout 2>&1) || RC=$?
  rc_is "cr-layout: an unreadable base commit fails closed" 1
  RC=0
  OUT=$(GATE_BASE_REF=--no-such-option "$BASH" "$repo/scripts/quality-gate.sh" --only cr-layout 2>&1) || RC=$?
  rc_is "cr-layout: a base value that looks like an option fails closed" 1
  git -C "$repo" rm -q config/project.yaml
  git -C "$repo" commit -q -m noconfig
  gate "$repo" --only cr-layout --base HEAD
  rc_is "cr-layout --base: a base without a configuration file fails closed" 1
  out_has "cr-layout: the failure says so" "fails closed"
}

# new-cr: creates the five files, refuses to overwrite, validates its arguments, has --dry-run (T-7).
test_new_cr() {
  local repo="$WORK/new-cr" n f before last cr12="docs/cr/${CRP}12"
  cr_stage "$repo" 5
  git -C "$repo" init -q -b main
  newcr() {
    RC=0
    OUT=$(cd "$repo" && python3 -I -B scripts/new-cr "$@" 2>&1) || RC=$?
  }
  digest() { (cd "$repo" && find docs -type f -exec sha256sum {} + | LC_ALL=C sort | sha256sum); }

  newcr --dry-run 12 add-thing
  rc_is "new-cr --dry-run exits 0" 0
  out_has "new-cr --dry-run says what it would do" "would create $cr12/"
  if [[ ! -e $repo/$cr12 ]]; then ok "new-cr --dry-run writes nothing"; else fail "new-cr --dry-run writes nothing"; fi
  newcr 12 add-thing
  rc_is "new-cr creates the skeleton" 0
  for f in "${CR_FILES[@]}"; do
    if [[ -s $repo/$cr12/$f ]]; then ok "new-cr writes $f"; else fail "new-cr writes $f"; fi
  done
  n=$(find "$repo/$cr12" -type f | wc -l)
  if [[ $n -eq 5 ]]; then ok "new-cr writes exactly five files"; else fail "new-cr writes exactly five files" "got $n"; fi
  if grep -q "^# ${CRP}12: " "$repo/$cr12/README.md" && grep -q '^| Issue | #12 |' "$repo/$cr12/README.md" &&
    grep -q "design/${CRP}12-add-thing" "$repo/$cr12/README.md"; then ok "new-cr fills in the number and the slug"; else fail "new-cr fills in the number and the slug"; fi
  if grep -rqE '<n>|<slug>' "$repo/$cr12"; then fail "new-cr leaves no <n> or <slug> placeholder"; else ok "new-cr leaves no <n> or <slug> placeholder"; fi
  last=$(tail -n 1 <<<"$OUT")
  if [[ $last == "new-cr: created $cr12/ with 5 files"* && ${#last} -le 120 ]]; then ok "new-cr ends with its summary line, within 120 characters"; else fail "new-cr ends with its summary line, within 120 characters" "$last"; fi
  commit_all "$repo" cr
  gate "$repo" --only cr-layout
  rc_is "cr-layout passes the skeleton that new-cr wrote" 0

  before=$(digest)
  newcr 12 add-thing
  rc_is "new-cr refuses to overwrite" 1
  out_has "new-cr names the existing directory" "already exists"
  newcr 12 other-slug
  rc_is "new-cr refuses to overwrite with another slug" 1
  if [[ $before == "$(digest)" ]]; then ok "new-cr changes nothing when it refuses"; else fail "new-cr changes nothing when it refuses"; fi
  rm "$repo/$cr12/design.md"
  newcr 12 add-thing
  rc_is "new-cr refuses a directory that lacks one file" 1
  if [[ ! -e $repo/$cr12/design.md ]]; then ok "new-cr does not complete a partial directory"; else fail "new-cr does not complete a partial directory"; fi
  mkdir "$repo/docs/cr/${CRP}13"
  newcr 13 empty-directory
  rc_is "new-cr refuses an existing empty directory" 1
  printf 'x\n' >"$repo/docs/cr/${CRP}14"
  newcr 14 a-file
  rc_is "new-cr refuses an existing file of that name" 1

  for n in 0 007 abc -1 '' 1.5 99999999999; do
    newcr "$n" ok-slug
    rc_is "new-cr rejects the number '$n'" 2
  done
  for n in Bad a--b -a a- 'a b' 'a/b' '../x' '' "$(printf 'a%.0s' {1..41})"; do
    newcr 20 "$n"
    rc_is "new-cr rejects the slug '${n:0:12}'" 2
  done
  newcr 20
  rc_is "new-cr needs both arguments" 2
  newcr 20 a b
  rc_is "new-cr rejects a third argument" 2
  if [[ ! -e $repo/docs/cr/${CRP}20 && ! -e $repo/docs/cr/${CRP}007 && ! -e $repo/docs/cr/${CRP}0 ]]; then ok "new-cr writes nothing for a rejected argument"; else fail "new-cr writes nothing for a rejected argument"; fi

  # a write error part way leaves nothing behind (never what existed before), and the next run succeeds
  newcr_failing() { # newcr_failing N SLUG K: scripts/new-cr with the K-th file write failing (disk full)
    RC=0
    OUT=$(cd "$repo" && python3 -I -B - "$1" "$2" "$3" 2>&1 <<'PY'
import builtins, errno, importlib.machinery, importlib.util, sys
number, slug, nth = sys.argv[1], sys.argv[2], int(sys.argv[3])
loader = importlib.machinery.SourceFileLoader("new_cr", "scripts/new-cr")
module = importlib.util.module_from_spec(importlib.util.spec_from_loader("new_cr", loader))
loader.exec_module(module)
real, count = builtins.open, [0]
def failing(path, mode="r", *args, **kwargs):
    if "x" in mode:
        count[0] += 1
        if count[0] == nth:
            raise OSError(errno.ENOSPC, "No space left on device")
    return real(path, mode, *args, **kwargs)
builtins.open = failing
sys.exit(module.main([number, slug]))
PY
    ) || RC=$?
  }
  tree() { (cd "$repo" && find . -path ./.git -prune -o -print | LC_ALL=C sort | sha256sum); }
  before=$(tree)
  newcr_failing 40 third-fails 3
  rc_is "new-cr: a write error on the third file is refused" 1
  out_has "new-cr: the write error is reported" "write failed"
  if [[ ! -e $repo/docs/cr/${CRP}40 ]]; then ok "new-cr: no directory is left after a write error"; else fail "new-cr: no directory is left after a write error"; fi
  if [[ $before == "$(tree)" ]]; then ok "new-cr: a write error changes nothing else (no temporary directory)"; else fail "new-cr: a write error changes nothing else (no temporary directory)"; fi
  newcr 40 third-fails
  rc_is "new-cr: the run after a write error succeeds" 0
  if [[ -f $repo/docs/cr/${CRP}40/implementation-plan.md ]]; then ok "new-cr: the second run writes all files"; else fail "new-cr: the second run writes all files"; fi
  newcr_failing 41 first-fails 1
  rc_is "new-cr: a write error on the first file is refused" 1
  if [[ ! -e $repo/docs/cr/${CRP}41 ]]; then ok "new-cr: nothing is left after a write error on the first file"; else fail "new-cr: nothing is left after a write error on the first file"; fi
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "fresh/"\n' >"$repo/config/project.yaml"
  before=$(tree)
  newcr_failing 42 new-root 3
  rc_is "new-cr: a write error in a root that has no cr/ directory is refused" 1
  if [[ $before == "$(tree)" && ! -e $repo/fresh ]]; then ok "new-cr: the directories this run made are removed again"; else fail "new-cr: the directories this run made are removed again"; fi
  newcr 42 new-root
  rc_is "new-cr: the run after that succeeds" 0

  # roots: the first entry of docs_roots by default, another entry with --root, nothing else
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "first/"\n    - "second/dir/"\n' >"$repo/config/project.yaml"
  newcr 30 in-first
  rc_is "new-cr writes into the first docs root" 0
  if [[ -f $repo/first/cr/${CRP}30/README.md ]]; then ok "new-cr: the default is the first root"; else fail "new-cr: the default is the first root"; fi
  newcr --root second/dir/ 31 in-second
  rc_is "new-cr --root takes another configured root" 0
  if [[ -f $repo/second/dir/cr/${CRP}31/README.md ]]; then ok "new-cr --root writes into that root"; else fail "new-cr --root writes into that root"; fi
  newcr --root docs/ 32 not-configured
  rc_is "new-cr --root refuses a root that is not configured" 2
  newcr --root ../outside/ 33 outside
  rc_is "new-cr --root refuses a path outside the repository" 2
  printf 'schema_version: 1\nworkflow:\n  docs_roots:\n    - "linked/"\n' >"$repo/config/project.yaml"
  mkdir "$WORK/new-cr-outside"
  ln -s "$WORK/new-cr-outside" "$repo/linked"
  newcr 34 through-a-link
  rc_is "new-cr refuses to write through a symbolic link" 1
  if [[ -z $(find "$WORK/new-cr-outside" -type f) ]]; then ok "new-cr wrote nothing through the link"; else fail "new-cr wrote nothing through the link"; fi
  rm "$repo/linked"
  printf 'schema_version: 1\nworkflow:\n  docs_root:\n    - "docs/"\n' >"$repo/config/project.yaml"
  newcr 35 bad-config
  rc_is "new-cr fails closed on a malformed configuration" 1
  rm "$repo/config/project.yaml"
  newcr 36 no-config
  rc_is "new-cr fails closed without a configuration" 1
  printf '%s' "$CRL_CONFIG" >"$repo/config/project.yaml"
  mv "$repo/docs/cr/_template" "$WORK/new-cr-skeleton"
  newcr 37 no-skeleton
  rc_is "new-cr refuses without the skeleton" 1
  if [[ -z $(find "$repo" -name '__pycache__' -o -name '*.pyc') ]]; then ok "new-cr leaves no bytecode"; else fail "new-cr leaves no bytecode"; fi
}

# headings_of FILE HEADING...: prints the first HEADING that is not a line of FILE and fails.
headings_of() {
  local file=$1 want
  shift
  for want in "$@"; do
    if ! grep -qxF -- "$want" "$file"; then
      printf '%s' "$want"
      return 1
    fi
  done
}

# The skeleton files and the ADR template have the headers and sections of the design (T-7).
test_record_templates() {
  local t="$ROOT/docs/cr/_template" f adr="$ROOT/docs/adr/_template.md" h=''
  for f in "${CR_FILES[@]}"; do
    if [[ -f $t/$f ]]; then ok "skeleton has $f"; else fail "skeleton has $f"; fi
  done
  if h=$(headings_of "$t/README.md" '## Summary' '## Files') && grep -q '^| Issue | ' "$t/README.md" && grep -q '^| Class | ' "$t/README.md" &&
    grep -q '^| Status | ' "$t/README.md" && grep -q '^| ADR | ' "$t/README.md"; then ok "skeleton README.md has the summary table, the summary, and the file table"; else fail "skeleton README.md has the summary table, the summary, and the file table" "$h"; fi
  if h=$(headings_of "$t/impact-analysis.md" '## What changes' '## Affected components' '## Security and privacy' '## Operational impact on the approver' \
    '## Dependencies and assumptions' '## Cost and capacity' '## Rollback' '## Alternatives'); then ok "skeleton impact-analysis.md has the eight sections"; else fail "skeleton impact-analysis.md has the eight sections" "$h"; fi
  if h=$(headings_of "$t/design.md" '## Decisions in this design' '## Questions' '## Decisions of the approver') && grep -q '^| Status | ' "$t/design.md"; then ok "skeleton design.md has the header table, the decisions table, and the sections"; else fail "skeleton design.md has the header table, the decisions table, and the sections" "$h"; fi
  if grep -q '^| ID | Criterion | Evidence |$' "$t/acceptance-criteria.md"; then ok "skeleton acceptance-criteria.md has the ID, Criterion, Evidence table"; else fail "skeleton acceptance-criteria.md has the ID, Criterion, Evidence table"; fi
  if h=$(headings_of "$t/implementation-plan.md" '## 1. The PRs' '## 2. Checklist (each PR keeps its own)' '## 3. User-only actions' '## 4. What this plan does not do'); then ok "skeleton implementation-plan.md has the PR table, checklist, user-only actions, and the not-do list"; else fail "skeleton implementation-plan.md has the PR table, checklist, user-only actions, and the not-do list" "$h"; fi
  if [[ -f $adr ]]; then ok "the ADR template exists"; else fail "the ADR template exists"; fi
  for h in Status Date Repositories CR Scope Related 'Amended by'; do
    if grep -q "^| $h | " "$adr"; then ok "the ADR template header has $h"; else fail "the ADR template header has $h"; fi
  done
  if h=$(headings_of "$adr" '## Context' '## Decision' '## Alternatives' '## Consequences' '## Open items' '## Acceptance criteria'); then ok "the ADR template has its six sections"; else fail "the ADR template has its six sections" "$h"; fi
  if grep -q "^# ${ADP}<n>: " "$adr"; then ok "the ADR template title carries the placeholder number"; else fail "the ADR template title carries the placeholder number"; fi
  # the owned templates cite no record by a real number, and the summary file is within its budget
  if grep -rEq "\b(CR|ADR)-[0-9]+\b" "$t" "$adr"; then fail "the owned templates cite no record number"; else ok "the owned templates cite no record number"; fi
  if [[ $(wc -l <"$t/README.md") -le 50 && $(wc -c <"$t/README.md") -le 4096 ]]; then ok "skeleton README.md is within the cr-readme budget"; else fail "skeleton README.md is within the cr-readme budget"; fi
}

# ---------------------------------------------------------------- owned-drift (the base commit, the branch of a sync or init PR, pin bumps)

OD_WORKFLOW=$'name: ci\non: push\npermissions: {}\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: actions/checkout@'"$(rep 20 a1)"$' # v4.0.0\n      - uses: actions/cache@'"$(rep 20 b2)"$' # v3.0.0\n      - run: echo hi\n'
OD_OWNED=".claude/rules/example.md .github/workflows/ci.yml"
# od_state REPO [VERSION]: .template/state.toml with the hashes of the current files of OD_OWNED
od_state() {
  local repo=$1 f
  mkdir -p "$repo/.template"
  {
    printf 'schema_version = 1\n\n[template]\nsource = "example-owner/template"\nversion = "%s"\ncommit = "%s"\n\n[owned]\n' "${2:-1.0.0}" "$(rep 20 c3)"
    for f in $OD_OWNED; do printf '"%s" = "%s"\n' "$f" "$(sha256sum "$repo/$f" | cut -d' ' -f1)"; done
    printf '\n[overrides]\n'
  } >"$repo/.template/state.toml"
}
# od_state_override REPO: the state of a sync that kept .claude/rules/example.md as an override (hash as the template ships it)
od_state_override() {
  sed -i 's|^\[overrides\]$|[overrides]\n".claude/rules/example.md" = "override"|' "$1/.template/state.toml"
}
# od_repo: a repository whose base commit holds the owned files and the state written with them (the commit of the last sync)
od_repo() {
  local repo
  repo=$(new_repo)
  mkdir -p "$repo/.claude/rules" "$repo/.github/workflows"
  printf '# Example rule\n' >"$repo/.claude/rules/example.md"
  printf '%s' "$OD_WORKFLOW" >"$repo/.github/workflows/ci.yml"
  od_state "$repo"
  commit_all "$repo" "sync"
  printf '%s\n' "$repo"
}
# od_run REPO BASE HEAD_REF: runs owned-drift against the base commit (BASE empty: no base) with the head branch name (empty: none)
od_run() {
  RC=0
  OUT=$(cd "$1" && env ${2:+GATE_BASE_REF="$2"} ${3:+GATE_HEAD_REF="$3"} "$BASH" scripts/quality-gate.sh --only owned-drift 2>&1) || RC=$?
}
od_head() { # od_head REPO: commit the working tree as the head and print the base commit (its parent)
  commit_all "$1" head
  git -C "$1" rev-parse HEAD~1
}
od_pass() { od_run "$2" "$3" "${4:-}"; rc_is "owned-drift: $1" 0; }
od_fail() { # od_fail NAME REPO BASE HEAD_REF EXPECT
  od_run "$2" "$3" "$4"
  rc_is "owned-drift: $1" 1
  out_has "owned-drift: $1 (finding)" "$5"
}
od_entry() { printf '[[override]]\npath = "%s"\nreason = "the project words it its own way"\npr = "#7"\n' "$1" >"$2/.template/overrides.toml"; }

test_owned_drift() {
  local repo base before

  # a change is judged against the hashes of the BASE commit: rewriting state.toml in the same PR does not help
  repo=$(od_repo)
  printf 'A line of the project.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo"
  base=$(od_head "$repo")
  od_fail "a PR that changes an owned file and rewrites its hash fails" "$repo" "$base" feature/x ".claude/rules/example.md:1: owned file changed without an entry in .template/overrides.toml"
  repo=$(od_repo)
  printf 'A line of the project.\n' >>"$repo/.claude/rules/example.md"
  od_entry .claude/rules/example.md "$repo"
  base=$(od_head "$repo")
  od_pass "the same change with an entry passes" "$repo" "$base" feature/x
  od_pass "without a base the tree is judged against its own state, and the entry is there" "$repo" "" ""

  # an entry in the base is not enough once the head drops it
  repo=$(od_repo)
  printf 'A line of the project.\n' >>"$repo/.claude/rules/example.md"
  od_entry .claude/rules/example.md "$repo"
  commit_all "$repo" "diverge, declared"
  rm "$repo/.template/overrides.toml"
  base=$(od_head "$repo")
  od_fail "a PR that drops the entry of a file that still differs fails, although it does not touch the file" "$repo" "$base" feature/x ".claude/rules/example.md:1: owned file changed without an entry"

  # a divergence that the PR does not touch is not the PR's business, but a push is judged as a whole
  repo=$(od_repo)
  printf 'A line of the project.\n' >>"$repo/.claude/rules/example.md"
  commit_all "$repo" "diverge, undeclared"
  printf 'x\n' >"$repo/notes.md"
  base=$(od_head "$repo")
  od_pass "a PR that leaves an undeclared earlier divergence alone passes" "$repo" "$base" feature/x
  od_fail "the tree itself, without a base, still fails" "$repo" "" "" ".claude/rules/example.md:1: owned file changed without an entry"

  # the exemption of a template-sync PR: branch name, the version of the branch in state.toml, and a state that matches the files
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.1.0
  base=$(od_head "$repo")
  od_pass "a template-sync PR that changes files and state.toml together passes" "$repo" "$base" template-sync/v1.1.0
  od_fail "the same change on another branch fails" "$repo" "$base" feature/x "owned file changed without an entry"
  od_fail "a branch that only starts like a sync branch fails" "$repo" "$base" template-sync/v1.1.0-x "owned file changed without an entry"
  od_fail "a sync branch for another version fails" "$repo" "$base" template-sync/v1.2.0 ".template/state.toml:1: a template-sync pull request must record the version of its branch"
  RC=0
  OUT=$(cd "$repo" && env GATE_BASE_REF="$base" GITHUB_HEAD_REF=template-sync/v1.1.0 "$BASH" scripts/quality-gate.sh --only owned-drift 2>&1) || RC=$?
  rc_is "owned-drift: GITHUB_HEAD_REF names the branch when GATE_HEAD_REF is not set" 0
  RC=0
  OUT=$(cd "$repo" && env GATE_BASE_REF="$base" GATE_HEAD_REF=feature/x GITHUB_HEAD_REF=template-sync/v1.1.0 "$BASH" scripts/quality-gate.sh --only owned-drift 2>&1) || RC=$?
  rc_is "owned-drift: GATE_HEAD_REF wins over GITHUB_HEAD_REF" 1
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.1.0
  printf 'A line that is not in the state.\n' >>"$repo/.claude/rules/example.md"
  base=$(od_head "$repo")
  od_fail "a sync PR whose files do not match the hashes it records fails" "$repo" "$base" template-sync/v1.1.0 ".claude/rules/example.md:1: does not match its hash in .template/state.toml"
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.1.0
  od_entry .claude/rules/example.md "$repo"
  base=$(od_head "$repo")
  od_fail "a sync PR that changes overrides.toml fails" "$repo" "$base" template-sync/v1.1.0 ".template/overrides.toml:1: a template-sync pull request must not change .template/overrides.toml"
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  base=$(od_head "$repo")
  od_fail "a sync PR that leaves state.toml alone fails" "$repo" "$base" template-sync/v1.1.0 "owned file changed without an entry"

  # a sync PR cannot change an owned file by dropping its line from [owned]: a path of the base state that the head no longer lists must be gone
  repo=$(od_repo)
  printf 'A line the template never shipped.\n' >>"$repo/.claude/rules/example.md"
  OD_OWNED=".github/workflows/ci.yml" od_state "$repo" 1.1.0
  base=$(od_head "$repo")
  od_fail "a sync PR that changes an owned file and drops it from [owned] fails" "$repo" "$base" template-sync/v1.1.0 ".claude/rules/example.md:1: dropped from [owned] of .template/state.toml but still present"
  repo=$(od_repo)
  rm "$repo/.claude/rules/example.md"
  OD_OWNED=".github/workflows/ci.yml" od_state "$repo" 1.1.0
  base=$(od_head "$repo")
  od_pass "a sync PR that removes an owned file and drops it from [owned] passes" "$repo" "$base" template-sync/v1.1.0
  repo=$(od_repo)
  od_entry .claude/rules/example.md "$repo"
  commit_all "$repo" "declare the divergence"
  OD_OWNED=".github/workflows/ci.yml" od_state "$repo" 1.1.0
  od_state_override "$repo"
  base=$(od_head "$repo")
  od_pass "a sync PR may keep a file that is recorded as an override and dropped from [owned]" "$repo" "$base" template-sync/v1.1.0

  repo=$(od_repo)
  printf 'schema_version = 1\nversion = "1.1.0"\n\n[classes]\nowned = [".github/"]\nseeded = [".claude/rules/example.md"]\ntemplate-only = []\n' >"$repo/.template/manifest.toml"
  OD_OWNED=".github/workflows/ci.yml" od_state "$repo" 1.1.0
  base=$(od_head "$repo")
  od_pass "a sync PR may leave a file that the head manifest turns into a seeded one" "$repo" "$base" template-sync/v1.1.0

  # the exemption needs a version greater than the base's
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.0.0
  base=$(od_head "$repo")
  od_fail "a sync PR that records the version of the base fails" "$repo" "$base" template-sync/v1.0.0 ".template/state.toml:1: a template-sync pull request must record a version greater than the base's (1.0.0)"
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 0.9.0
  base=$(od_head "$repo")
  od_fail "a sync PR that records a lower version fails" "$repo" "$base" template-sync/v0.9.0 ".template/state.toml:1: a template-sync pull request must record a version greater than the base's (1.0.0)"
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.0.1
  base=$(od_head "$repo")
  od_pass "a sync PR that records a higher version passes" "$repo" "$base" template-sync/v1.0.1
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.10.0
  base=$(od_head "$repo")
  od_pass "versions are compared as numbers" "$repo" "$base" template-sync/v1.10.0

  # the exemption of an init PR: the base has no state.toml, the head writes it with the files
  repo=$(new_repo)
  mkdir -p "$repo/.claude/rules" "$repo/.github/workflows"
  printf '# Example rule\n' >"$repo/.claude/rules/example.md"
  printf '%s' "$OD_WORKFLOW" >"$repo/.github/workflows/ci.yml"
  commit_all "$repo" "template"
  od_state "$repo" 0.0.0
  base=$(od_head "$repo")
  od_pass "an init PR that adds state.toml passes" "$repo" "$base" init/v0.0.0
  od_fail "state.toml added by any other PR fails" "$repo" "$base" feature/x ".template/state.toml:1: added outside an init pull request"
  od_fail "state.toml added without a branch name fails" "$repo" "$base" "" ".template/state.toml:1: added outside an init pull request"
  repo=$(od_repo)
  printf 'A line of the new version.\n' >>"$repo/.claude/rules/example.md"
  od_state "$repo" 1.1.0
  base=$(od_head "$repo")
  od_fail "an init branch is no exemption when the base already has state.toml" "$repo" "$base" init/v1.1.0 "owned file changed without an entry"
  repo=$(new_repo)
  commit_all "$repo" "template"
  printf 'x\n' >"$repo/notes.md"
  base=$(od_head "$repo")
  od_run "$repo" "$base" feature/x
  rc_is "owned-drift: a repository without state.toml at the base and the head has nothing to judge (exit 0 under the gate: SKIP)" 0
  out_has "owned-drift: it is reported as skipped" "SKIP owned-drift"

  # a pin bump of an owned workflow: a change limited to uses: lines of the same actions is allowed (the reading to confirm)
  repo=$(od_repo)
  sed -i "s/actions\/checkout@$(rep 20 a1)/actions\/checkout@$(rep 20 d4)/; s/# v4.0.0/# v4.1.0/" "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_pass "a pin bump of one action in an owned workflow passes" "$repo" "$base" feature/x
  sed -i "s/actions\/cache@$(rep 20 b2)/actions\/cache@$(rep 20 e5)/; s/# v3.0.0/# v3.1.0/" "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_pass "a second bump on top of the first passes (it is measured against the synced version)" "$repo" "$base" feature/x
  printf 'x\n' >"$repo/notes.md"
  base=$(od_head "$repo")
  od_pass "a PR that does not touch the bumped workflow passes" "$repo" "$base" feature/x
  od_pass "the tree itself, without a base, is within the pin-bump reading too (the synced version is the last commit of state.toml)" "$repo" "" ""
  sed -i 's/echo hi/echo changed/' "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_fail "a change of another line on top of a bump fails" "$repo" "$base" feature/x ".github/workflows/ci.yml:1: owned file changed without an entry"
  repo=$(od_repo)
  sed -i "s/actions\/checkout@$(rep 20 a1) # v4.0.0/actions\/other@$(rep 20 d4) # v4.1.0/" "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_fail "a uses: line that names another action fails" "$repo" "$base" feature/x ".github/workflows/ci.yml:1: owned file changed without an entry"
  repo=$(od_repo)
  sed -i "s/      - run: echo hi/      - run: echo hi\n      - uses: actions\/checkout@$(rep 20 d4) # v4.1.0/" "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_fail "a new uses: line fails" "$repo" "$base" feature/x ".github/workflows/ci.yml:1: owned file changed without an entry"
  repo=$(od_repo)
  printf '# an owned file that is not a workflow\n' >>"$repo/.claude/rules/example.md"
  base=$(od_head "$repo")
  od_fail "a pin-bump reading does not reach a file that is not a workflow" "$repo" "$base" feature/x ".claude/rules/example.md:1: owned file changed without an entry"
  # the reading is one constant: with it off a pin bump needs an entry like any other change
  repo=$(od_repo)
  sed -i 's/^OWNED_DRIFT_ALLOWS_USES_ONLY = True$/OWNED_DRIFT_ALLOWS_USES_ONLY = False/' "$repo/scripts/lib/template_sync/uses_drift.py"
  commit_all "$repo" "the other reading"
  sed -i "s/actions\/checkout@$(rep 20 a1)/actions\/checkout@$(rep 20 d4)/; s/# v4.0.0/# v4.1.0/" "$repo/.github/workflows/ci.yml"
  base=$(od_head "$repo")
  od_fail "with the other reading a pin bump fails" "$repo" "$base" feature/x ".github/workflows/ci.yml:1: owned file changed without an entry"

  # without history the synced version is unknown: a pin bump then needs an entry (the check fails safe)
  repo=$(od_repo)
  sed -i "s/actions\/checkout@$(rep 20 a1)/actions\/checkout@$(rep 20 d4)/; s/# v4.0.0/# v4.1.0/" "$repo/.github/workflows/ci.yml"
  commit_all "$repo" head
  git -C "$repo" checkout -q --orphan flat
  git -C "$repo" commit -q -m flat
  printf 'x\n' >>"$repo/notes.md"
  commit_all "$repo" more
  od_fail "a pin bump already in a history without the sync commit fails safe" "$repo" "" "" ".github/workflows/ci.yml:1: owned file changed without an entry"

  # an unreadable or option-like base fails closed
  repo=$(od_repo)
  od_fail "an unreadable base commit fails closed" "$repo" no-such-ref feature/x "cannot read the base commit"
  od_fail "a base value that looks like an option fails closed" "$repo" --no-such-option feature/x "cannot read the base commit"
  git -C "$repo" rm -q -r .template
  commit_all "$repo" "no state"
  printf 'x\n' >>"$repo/.claude/rules/example.md"
  base=$(od_head "$repo")
  od_run "$repo" "$base" feature/x
  rc_is "owned-drift: a base without state.toml and a head without it has nothing to judge" 0

  # no state file anywhere: nothing applies
  repo=$(new_repo)
  commit_all "$repo" "template"
  gate "$repo" --only owned-drift
  rc_is "owned-drift: a tree without state.toml passes" 0
  out_has "owned-drift: and says it was skipped" "SKIP owned-drift"
}

# ---------------------------------------------------------------- main

if want dispatcher; then test_dispatcher; fi
if want classes; then test_classes; fi
if want workflow-policy; then test_workflow_policy; fi
if want claude-md; then test_claude_md_scope; fi
if want operator-contract; then test_operator_contract; fi
if want operator_guard; then test_operator_guard; fi
if want cr-layout; then test_cr_layout_policy; fi
if want new-cr; then test_new_cr; fi
if want cr-layout || want new-cr; then test_record_templates; fi
if want instructions; then test_instruction_files; fi
if want owned-drift; then test_owned_drift; fi
run_check_fixtures
for f in "$CHECKS"/*.sh; do
  id=$(basename "$f" .sh)
  if want "$id" && [[ -d $FIXTURES/$id ]]; then run_notool "$id"; fi
done
if want python; then
  tmp=$(mktemp -d "$WORK/bytecode.XXXXXX")
  cp -R "$FIXTURES/python/pass-ok/." "$tmp/"
  files=$(list_files "$tmp")
  (cd "$tmp" && "$BASH" "$CHECKS/python.sh" <<<"$files" >/dev/null 2>&1) || true
  if [[ -z $(find "$tmp" -name '__pycache__' -o -name '*.pyc') ]]; then ok "python: writes no bytecode"; else fail "python: writes no bytecode"; fi
fi
if want yaml; then
  run_adapters yaml yamllint 'a.yml:3:1: [error] too many blank lines (1 > 0) (empty-lines)' \
    'a.yml:3: [error] too many blank lines (1 > 0) (empty-lines)' a.yml
fi
if want markdown; then
  run_adapters markdown pymarkdown 'a.md:3:1: MD018: No space present after the hash character (no-missing-space-atx)' \
    'a.md:3: MD018: No space present after the hash character (no-missing-space-atx)' a.md
fi

printf 'tests: %d passed, %d failed, %d skipped\n' "$PASSED" "$FAILED" "$SKIPPED"
if [[ $FAILED -gt 0 ]]; then exit 1; fi
