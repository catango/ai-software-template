#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "repos/o/r/pulls/7/merge" -f merge_method=squash
