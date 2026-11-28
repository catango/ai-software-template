#!/usr/bin/env bash
# Run the template's own tests (standard library only, no network).
#   template-dev/run-tests.sh [manifest|leak|engine|ci|gate|skills|instructions|sync|all]
#
# manifest  T-1 and the generation part of T-2
# leak      T-15 and the scan part of T-21
# engine    the install engine and the CI manifest (T-14: both architectures, checksums)
# ci        the CI matrix, the `not run` summary, and the private-flag condition (T-3, gate part)
# skills    the fixture tests of scripts/pr-contract and its rules (T-8, T-20), and of the Skill scripts and the six Skills (T-12, T-13:
#           pr-checkpoint, cr-context with cr-preflight, cr-criteria); later rows add their script tests to tests/skills/
# instructions  T-4, T-5, T-6, T-22 on the template and on generated trees (the checks' fixtures are in `gate`)
# sync      T-9, T-10, the lag line, and the classes of the files of row 8 (template-sync against local git repositories)
# gate      the gate's fixtures (tests/quality-gate/run.sh: one passing and failing case per check; T-19 forbidden-api)
set -euo pipefail

usage() {
  echo "usage: ${0##*/} [manifest|leak|engine|ci|gate|skills|instructions|sync|all]" >&2
  exit 2
}

[ "$#" -le 1 ] || usage
target=${1:-all}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1

skills() {
  (cd "$here/tests/pr_contract" && python3 -B -s -m unittest discover -s . -p 'test_*.py')
  (cd "$here/tests/skills" && python3 -B -s -m unittest discover -s . -p 'test_*.py')
}
sync_tests() { (cd "$here/tests/sync" && python3 -B -s -m unittest discover -s . -p 'test_*.py'); }
unit() { (cd "$here/tests" && python3 -B -s -m unittest "$@"); }

case "$target" in
  manifest) unit test_manifest ;;
  leak) unit test_leak ;;
  ci) unit test_ci ;;
  instructions) unit test_instructions ;;
  engine) (cd "$here/tests/toolchain" && python3 -B -s -m unittest discover -s . -p 'test_*.py') ;;
  skills) skills ;;
  sync) sync_tests ;;
  gate) "$here/../tests/quality-gate/run.sh" ;;
  all)
    unit test_manifest test_leak test_ci test_instructions
    (cd "$here/tests/toolchain" && python3 -B -s -m unittest discover -s . -p 'test_*.py')
    skills
    sync_tests
    "$here/../tests/quality-gate/run.sh"
    ;;
  *) usage ;;
esac
