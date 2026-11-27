#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "repos/$OWNER/$REPO/secret-scanning/alerts/$N" -f state=resolved
