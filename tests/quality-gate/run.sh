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
# The policy base commit  is set by the tests that need it, never inherited.
unset GATE_BASE_REF

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
  secret=$(printf '%s.%s.%s.%s' 192 0 2 1)
  mkdir -p "$d/pass-doc-range-and-noreply"
  printf '%s\n%s\n%s\n' "$secret" "1234+bot${at}users.noreply.github.com" "$(printf '%s.%s.%s.%s/%s' 0 0 0 0 0)" >"$d/pass-doc-range-and-noreply/file.txt"
}

run_check_fixtures() {
  local id name dir
  gen_hygiene_cases
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
  expected="bash-hygiene shellcheck public-repo-hygiene workflow-policy forbidden-api no-claude-in-workflows links yaml markdown size-budget actionlint toolchain python gate-tests"
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

# ---------------------------------------------------------------- main

if want dispatcher; then test_dispatcher; fi
if want classes; then test_classes; fi
if want workflow-policy; then test_workflow_policy; fi
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
