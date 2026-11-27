#!/usr/bin/env bash
set -euo pipefail
gh api "$base/merge" -X PUT
