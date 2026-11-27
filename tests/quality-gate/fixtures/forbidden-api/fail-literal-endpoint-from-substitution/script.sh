#!/usr/bin/env bash
set -euo pipefail
gh api "$(printf %s repos/o/r)/pulls/1/merge" -X PUT
