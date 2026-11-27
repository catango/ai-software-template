#!/usr/bin/env bash
set -euo pipefail
echo go | gh api -X PUT "repos/$OWNER/$REPO/pulls/$N/merge"
