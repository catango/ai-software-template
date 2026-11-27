#!/usr/bin/env bash
set -euo pipefail
curl -sS -d '{}' https://api.github.com/repos/$OWNER/$REPO
