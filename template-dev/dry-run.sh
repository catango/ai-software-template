#!/usr/bin/env bash
# The scripted dry run of design section 12 (a), steps 1 to 4 and 8: generate a repository from the template and check it.
#   template-dev/dry-run.sh --template DIR --config FILE --out EMPTY_DIR
# Template-only: initialize-project removes it. Needs no network, no GitHub repository, no credential, and no cloud resource.
# Writes only inside --out (the generated repository in OUT/repo, logs and scratch files in OUT/work); the template is only read.
# Steps 5 to 7 (pr-contract fixtures, sync, review-threads) are run by the jobs `skills` and `sync` of template-ci.yml.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
helper="$here/lib/dry_run_checks.py"

usage() {
  cat <<'USAGE'
usage: template-dev/dry-run.sh --template DIR --config FILE --out EMPTY_DIR

Generates a repository from the template in DIR with the configuration FILE and checks it, as step 1 to 4 and step 8 of the
scripted dry run of the design (section 12 (a)):

  1 export      the files of the template without history (what "Use this template" copies)
  2 initialize  scripts/initialize-project --config; no template-only path is left; every file has one class
  3 gate        git init, commit, ./scripts/quality-gate.sh --strict in the generated repository
  4 new-cr      scripts/new-cr 1 sample; cr-layout passes it and fails a missing file and an unqualified reference
  8 leak        no identifier, platform reference, fixed architecture, or unfilled placeholder in the payload and the generated tree

--out must be an existing, empty directory outside the template. The script writes only inside it and says so: the generated
repository is OUT/repo, the logs and scratch files are in OUT/work. The template is only read. Nothing is fetched or pushed.

The report goes to standard output, one line per step and a last summary line:
  dry-run: step=<n> name=<name> result=<pass|fail|skip> [detail=<text>]
  dry-run: summary result=<pass|fail> steps=<n> passed=<n> failed=<n> skipped=<n> config=<file name> arch=<machine name>
Diagnostics go to standard error. Exit status: 0 every step passed, 1 a step failed, 2 a refusal or usage error.
USAGE
}

usage_error() {
  printf 'dry-run: %s\n' "$1" >&2
  usage >&2
  exit 2
}

refuse() {
  printf 'dry-run: %s\n' "$1" >&2
  exit 2
}

template='' config='' out=''
set_once() { # set_once NAME VALUE-IF-SET
  [[ -z ${!1} ]] || usage_error "$2 given twice"
}
while [[ $# -gt 0 ]]; do
  case $1 in
    -h | --help)
      usage
      exit 0
      ;;
    --template | --config | --out)
      [[ $# -ge 2 && -n $2 ]] || usage_error "$1 needs a value"
      case $1 in
        --template) set_once template --template && template=$2 ;;
        --config) set_once config --config && config=$2 ;;
        --out) set_once out --out && out=$2 ;;
      esac
      shift
      ;;
    *) usage_error "unknown argument: $1" ;;
  esac
  shift
done
[[ -n $template && -n $config && -n $out ]] || usage_error "--template, --config and --out are all required"

# Refusals come before anything is written.
[[ -d $template ]] || refuse "--template: not a directory: $template"
template=$(cd "$template" && pwd -P)
top=$(git -C "$template" rev-parse --show-toplevel 2>/dev/null) || refuse "--template: not a git repository: $template"
[[ $(cd "$top" && pwd -P) == "$template" ]] || refuse "--template: not the top of a git repository: $template"
[[ -f $template/.template/manifest.toml ]] || refuse "--template: no .template/manifest.toml, so not a template: $template"
[[ -f $config ]] || refuse "--config: not a file: $config"
config=$(cd "$(dirname "$config")" && pwd -P)/$(basename "$config")
[[ -d $out && ! -L $out ]] || refuse "--out: not an existing directory (a link is refused too): $out"
out=$(cd "$out" && pwd -P)
case $out/ in
  "$template"/*) refuse "--out: inside the template: $out" ;;
  *) ;;
esac
[[ -z $(find "$out" -mindepth 1 -maxdepth 1 -print -quit) ]] || refuse "--out: not empty: $out"

repo=$out/repo
work=$out/work
mkdir "$repo" "$work" "$work/tmp" "$work/home" "$work/cache"
printf 'dry-run: writing only inside %s\n' "$out" >&2

# One environment for everything that runs in the generated repository (initialize-project, git, the gate, new-cr): scratch and
# cache files stay inside --out, git reads no configuration of the machine, and nothing asks for a credential. The export and
# the leak scan read the template with the caller's own git configuration, so a file the caller's git ignores is not exported.
iso=(env "TMPDIR=$work/tmp" "HOME=$work/home" "XDG_CACHE_HOME=$work/cache" "XDG_CONFIG_HOME=$work/home/.config"
  PYTHONDONTWRITEBYTECODE=1 GIT_TERMINAL_PROMPT=0 GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_CONFIG_NOSYSTEM=1
  GIT_AUTHOR_NAME=dry-run GIT_AUTHOR_EMAIL=noreply GIT_COMMITTER_NAME=dry-run GIT_COMMITTER_EMAIL=noreply)
export PYTHONDONTWRITEBYTECODE=1

total=0 passed=0 failed=0 skipped=0
detail=''

emit() { # emit STEP NAME RESULT DETAIL
  local text
  text=$(printf '%s' "$4" | LC_ALL=C tr -c '[:print:]' ' ' | cut -c1-200)
  total=$((total + 1))
  case $3 in
    pass) passed=$((passed + 1)) ;;
    fail) failed=$((failed + 1)) ;;
    *) skipped=$((skipped + 1)) ;;
  esac
  if [[ -n $text ]]; then
    printf 'dry-run: step=%s name=%s result=%s detail=%s\n' "$1" "$2" "$3" "$text"
  else
    printf 'dry-run: step=%s name=%s result=%s\n' "$1" "$2" "$3"
  fi
}

run_step() { # run_step STEP NAME FUNCTION: the function sets `detail` and returns 0 or 1
  detail=''
  if "$3"; then emit "$1" "$2" pass "$detail"; else emit "$1" "$2" fail "$detail"; fi
}

show_tail() { # show_tail FILE: the end of a log goes to standard error
  tail -n 30 "$1" >&2 || true
}

tree_ready=0 repo_ready=0
commit=$(git -C "$template" rev-parse --verify --quiet HEAD 2>/dev/null || true)

commit_all() { # commit_all DIR MESSAGE
  "${iso[@]}" git -C "$1" add -A >>"$work/git.log" 2>&1 || return 1
  "${iso[@]}" git -C "$1" commit --quiet --no-gpg-sign -m "$2" >>"$work/git.log" 2>&1
}

step_export() {
  local result
  result=$(python3 -I -B "$helper" export "$template" "$repo" 2>"$work/export.log") || {
    show_tail "$work/export.log"
    detail='the export failed'
    return 1
  }
  detail=$result
}

step_initialize() {
  local args=(--config "$config")
  [[ -z $commit ]] || args+=(--template-commit "$commit")
  if ! (cd "$repo" && "${iso[@]}" python3 -B scripts/initialize-project "${args[@]}") >"$work/init.log" 2>&1; then
    show_tail "$work/init.log"
    detail='initialize-project refused or failed'
    return 1
  fi
  if ! python3 -I -B "$helper" tree "$repo" >"$work/tree.log" 2>&1; then
    show_tail "$work/tree.log"
    detail="the generated tree is wrong: $(wc -l <"$work/tree.log") finding(s)"
    return 1
  fi
  tree_ready=1
}

step_gate() {
  if ! "${iso[@]}" git -C "$repo" init --quiet --template= -b main >"$work/git.log" 2>&1 ||
    ! commit_all "$repo" "Generated from the template (dry run)"; then
    show_tail "$work/git.log"
    detail='git init or commit failed in the generated repository'
    return 1
  fi
  repo_ready=1
  local rc=0
  (cd "$repo" && "${iso[@]}" ./scripts/quality-gate.sh --strict) >"$work/gate.log" 2>&1 || rc=$?
  if [[ $rc -ne 0 ]]; then
    show_tail "$work/gate.log"
    detail="quality-gate --strict exited $rc"
    return 1
  fi
  if [[ -n $("${iso[@]}" git -C "$repo" status --porcelain --untracked-files=all) ]]; then
    "${iso[@]}" git -C "$repo" status --porcelain --untracked-files=all >&2
    detail='quality-gate --strict left changes in the generated repository'
    return 1
  fi
}

# cr-layout through the gate's entry point, on the trial copy; the status is returned.
cr_layout() {
  local rc=0
  (cd "$trial" && "${iso[@]}" ./scripts/quality-gate.sh --strict --only cr-layout) >"$work/cr-layout.log" 2>&1 || rc=$?
  return "$rc"
}

# cr_layout_rejects PATTERN WHAT: cr-layout exits 1 and its log has a finding that matches PATTERN (an extended expression over the
# stable part of the finding, never over user text). Sets `detail` on failure.
cr_layout_rejects() {
  local rc=0
  cr_layout || rc=$?
  if [[ $rc -ne 1 ]]; then
    detail="cr-layout accepted $2 (exit $rc)"
    return 1
  fi
  if ! grep -Eq -- "$1" "$work/cr-layout.log"; then
    show_tail "$work/cr-layout.log"
    detail="cr-layout rejected $2, but not with the finding for it"
    return 1
  fi
}

# restore_trial WHAT FILE: undo the changes of a negative case in the trial copy and check that it is clean and FILE is back.
restore_trial() {
  if ! "${iso[@]}" git -C "$trial" checkout --quiet -- . >>"$work/git.log" 2>&1; then
    show_tail "$work/git.log"
    detail="git checkout failed: the trial copy was not restored after $1"
    return 1
  fi
  if [[ ! -f $2 || -n $("${iso[@]}" git -C "$trial" status --porcelain --untracked-files=all) ]]; then
    detail="the trial copy is not restored after $1: it differs from its commit"
    return 1
  fi
}

step_new_cr() {
  trial=$work/cr-trial
  cp -a "$repo" "$trial"
  if ! (cd "$trial" && "${iso[@]}" python3 -B scripts/new-cr 1 sample) >"$work/new-cr.log" 2>&1; then
    show_tail "$work/new-cr.log"
    detail='new-cr 1 sample failed'
    return 1
  fi
  if ! cr_layout; then
    show_tail "$work/cr-layout.log"
    detail='cr-layout rejected the sample change request'
    return 1
  fi
  local dir
  dir=$(find "$trial" -type d -path '*/cr/CR-1' -not -path "$trial/.git/*" -print -quit)
  [[ -n $dir ]] || {
    detail='the sample change request is not where new-cr says'
    return 1
  }
  if ! commit_all "$trial" "Sample change request"; then
    detail='git commit failed in the trial copy'
    return 1
  fi
  # Each negative case is set up, judged, and undone with every command checked (the steps run inside `if`, where `set -e` is off). A
  # case passes only when cr-layout exits 1 AND its log has the finding for that case, so another failure cannot stand in for it.
  rm -- "$dir/impact-analysis.md" || {
    detail='could not remove impact-analysis.md from the trial copy'
    return 1
  }
  cr_layout_rejects 'impact-analysis\.md:1: the change request has no readable regular file impact-analysis\.md' \
    'a change request with a missing file' || return 1
  restore_trial 'the missing-file case' "$dir/impact-analysis.md" || return 1
  printf '\nSee sample-design#12 for the design.\n' >>"$dir/design.md" || {
    detail='could not add the unqualified reference to design.md in the trial copy'
    return 1
  }
  cr_layout_rejects 'design\.md:[0-9]+: unqualified reference to another repository' \
    'an unqualified reference to another repository' || return 1
  if grep -Eq 'no readable regular file' "$work/cr-layout.log"; then
    detail='the unqualified-reference case still saw a missing file: the trial copy was not restored'
    return 1
  fi
  restore_trial 'the reference case' "$dir/impact-analysis.md" || return 1
}

step_leak() {
  local rc=0
  python3 -I -B "$helper" leak "$template" "$repo" >"$work/leak.log" 2>&1 || rc=$?
  if [[ $rc -ne 0 ]]; then
    cat "$work/leak.log" >&2
    detail="$(wc -l <"$work/leak.log") finding(s); the log names pattern and location only"
    return 1
  fi
}

skip_step() { emit "$1" "$2" skip "$3"; }

run_step 1 export step_export
if [[ $failed -eq 0 ]]; then
  run_step 2 initialize step_initialize
else
  skip_step 2 initialize "step 1 failed"
fi
if [[ $tree_ready -eq 1 ]]; then
  run_step 3 gate step_gate
else
  skip_step 3 gate "the generated tree is not ready"
fi
if [[ $repo_ready -eq 1 ]]; then
  run_step 4 new-cr step_new_cr
else
  skip_step 4 new-cr "the generated repository is not committed"
fi
if [[ $tree_ready -eq 1 ]]; then
  run_step 8 leak step_leak
else
  skip_step 8 leak "the generated tree is not ready"
fi

arch=$(python3 -I -B "$helper" host-arch)
result=pass
[[ $failed -eq 0 && $skipped -eq 0 ]] || result=fail
printf 'dry-run: summary result=%s steps=%s passed=%s failed=%s skipped=%s config=%s arch=%s\n' \
  "$result" "$total" "$passed" "$failed" "$skipped" "$(basename "$config")" "$arch"
[[ $result == pass ]]
