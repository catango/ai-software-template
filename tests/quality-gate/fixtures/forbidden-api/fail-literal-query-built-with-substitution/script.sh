#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query="$(cat query.txt)"
