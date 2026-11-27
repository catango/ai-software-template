#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/statuses/$SHA" -f state=success
