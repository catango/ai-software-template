#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "repos/$OWNER/$REPO/dependabot/alerts/$N" -f state=dismissed
