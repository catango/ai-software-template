#!/usr/bin/env bash
set -euo pipefail
gh api --method PUT "repos/$OWNER/$REPO/pulls/$N/merge"
