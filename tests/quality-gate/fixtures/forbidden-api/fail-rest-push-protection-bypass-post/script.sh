#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/secret-scanning/push-protection-bypasses" -f reason=false_positive
