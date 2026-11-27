#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/pulls/$N/reviews/$ID/events" -f event=APPROVE
