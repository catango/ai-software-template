#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/check-suites" -f head_sha=x
