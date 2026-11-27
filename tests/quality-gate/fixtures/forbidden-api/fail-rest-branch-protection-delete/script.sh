#!/usr/bin/env bash
set -euo pipefail
gh api -X DELETE "repos/$OWNER/$REPO/branches/$BRANCH/protection"
