#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "repos/$OWNER/$REPO/check-runs/$ID" -f conclusion=success
