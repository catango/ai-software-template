#!/usr/bin/env bash
set -euo pipefail
gh api -X POST "repos/$OWNER/$REPO/releases" -f tag_name=v1.0.0
