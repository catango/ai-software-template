#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "repos/$OWNER/$REPO/pulls/$N/reviews/$ID" -f body=x
