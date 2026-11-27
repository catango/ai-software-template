#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/hooks" -f name=web
