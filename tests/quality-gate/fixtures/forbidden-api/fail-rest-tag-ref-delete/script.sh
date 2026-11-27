#!/usr/bin/env bash
set -euo pipefail
gh api -X DELETE "repos/$OWNER/$REPO/git/refs/tags/v1.0.0"
