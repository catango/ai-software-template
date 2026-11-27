#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/git/refs" -f ref=refs/tags/v1.0.0
