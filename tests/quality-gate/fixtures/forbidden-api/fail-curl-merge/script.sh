#!/usr/bin/env bash
set -euo pipefail
curl -sS -X PUT "https://api.github.com/repos/$OWNER/$REPO/pulls/$N/merge"
