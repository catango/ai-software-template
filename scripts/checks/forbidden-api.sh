#!/usr/bin/env bash
# tool: python3
# order: 45
# class: template
# Scripts do not call what the approver alone may do: merge, review submission, auto-merge, rulesets, collaborators,
# repository settings, commit statuses and check runs, alert states, push protection, branch protection, secrets and
# variables, environments, webhooks, deploy keys, Actions permissions and runners, security-feature toggles, releases,
# tag refs, `git tag`, and the gh groups that do the same. Endpoints and GraphQL documents must be string literals, for any
# client. The list is data: forbidden-api.toml. Findings name the class, never script text. Not removable by an override.
set -euo pipefail
# shellcheck source=../lib/gate-lib.sh source-path=SCRIPTDIR
. "$(dirname "${BASH_SOURCE[0]}")/../lib/gate-lib.sh"

here="$(dirname "${BASH_SOURCE[0]}")"
files=$(cat)
mapfile -t scripts < <(
  while IFS= read -r f; do
    [[ -n $f && -f $f ]] || continue
    case $f in
      .github/workflows/*.yml | .github/workflows/*.yaml | .github/actions/*.yml | .github/actions/*.yaml | *.graphql | *.gql) printf '%s\n' "$f" ;;
      *) if gate_is_shell "$f" || [[ $f == *.py || $(gate_shebang "$f") =~ ^#!.*python ]]; then printf '%s\n' "$f"; fi ;;
    esac
  done <<<"$files"
)
if [[ ${#scripts[@]} -eq 0 ]]; then exit 77; fi
gate_require_tool python3

tracked=$(mktemp)
trap 'rm -f "$tracked"' EXIT
printf '%s\n' "$files" >"$tracked"
rc=0
printf '%s\n' "${scripts[@]}" | python3 -I -B "$here/forbidden_api.py" "$here/forbidden-api.toml" "$tracked" || rc=$?
exit "$rc"
