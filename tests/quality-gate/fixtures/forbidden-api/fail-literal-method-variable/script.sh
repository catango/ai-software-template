#!/usr/bin/env bash
set -euo pipefail
gh api -X "$METHOD" "repos/$OWNER/$REPO/pulls/$N/merge"
