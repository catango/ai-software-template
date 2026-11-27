#!/usr/bin/env bash
# Run the template's own tests (standard library only, no network).
#
#   template-dev/run-tests.sh [manifest|leak|all]
#
# manifest  T-1 and the generation part of T-2
# leak      T-15 and the scan part of T-21
set -euo pipefail

usage() {
  echo "usage: ${0##*/} [manifest|leak|all]" >&2
  exit 2
}

[ "$#" -le 1 ] || usage
target=${1:-all}

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
export PYTHONDONTWRITEBYTECODE=1

case "$target" in
  manifest) modules=(test_manifest) ;;
  leak) modules=(test_leak) ;;
  all) modules=(test_manifest test_leak) ;;
  *) usage ;;
esac

cd "$here/tests"
exec python3 -B -s -m unittest "${modules[@]}"
