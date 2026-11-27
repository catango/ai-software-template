#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "https://api.github.com/repos/$OWNER/$REPO/pulls/$N/merge"
