#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "repos/$OWNER/$REPO/actions/secrets/$NAME" -f encrypted_value=x
