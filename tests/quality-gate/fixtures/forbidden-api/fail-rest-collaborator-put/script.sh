#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "repos/$OWNER/$REPO/collaborators/$USER_NAME" -f permission=push
