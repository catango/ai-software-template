#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "repos/$OWNER/$REPO/code-scanning/alerts/$N" -f state=dismissed
