#!/usr/bin/env bash
# Run the template's own tests (standard library only, no network).
#
#   template-dev/run-tests.sh [manifest|leak|engine|ci|gate|all]
#
# manifest  T-1 and the generation part of T-2
# leak      T-15 and the scan part of T-21
# engine    the install engine and the CI manifest (T-14: both architectures, checksums)
# ci        the CI matrix, the `not run` summary, and the private-flag condition (T-3, gate part)
# gate      the gate's fixtures (tests/quality-gate/run.sh: one passing and failing case per check; T-19 forbidden-api)
set -euo pipefail

usage() {
  echo "usage: ${0##*/} [manifest|leak|engine|ci|gate|all]" >&2
  exit 2
}

[ "$#" -le 1 ] || usage
target=${1:-all}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1

unit() { (cd "$here/tests" && python3 -B -s -m unittest "$@"); }

case "$target" in
  manifest) unit test_manifest ;;
  leak) unit test_leak ;;
  ci) unit test_ci ;;
  engine) (cd "$here/tests/toolchain" && python3 -B -s -m unittest discover -s . -p 'test_*.py') ;;
  gate) "$here/../tests/quality-gate/run.sh" ;;
  all)
    unit test_manifest test_leak test_ci
    (cd "$here/tests/toolchain" && python3 -B -s -m unittest discover -s . -p 'test_*.py')
    "$here/../tests/quality-gate/run.sh"
    ;;
  *) usage ;;
esac
