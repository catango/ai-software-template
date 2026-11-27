#!/usr/bin/env bash
set -euo pipefail
gh api -X PATCH "repos/$OWNER/$REPO/code-scanning/default-setup" -f state=configured
