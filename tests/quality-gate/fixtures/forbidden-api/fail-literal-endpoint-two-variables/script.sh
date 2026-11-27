#!/usr/bin/env bash
set -euo pipefail
gh api "repos/$OWNER/$REPO/pulls/$N/$ACTION" -X PUT
