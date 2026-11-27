#!/usr/bin/env bash
set -euo pipefail
result=$(gh api "repos/$OWNER/$REPO/pulls/$N/reviews" -f event=APPROVE)
echo "$result"
