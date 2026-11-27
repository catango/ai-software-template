#!/usr/bin/env bash
set -euo pipefail
curl -sS --request POST "https://api.github.com/repos/$OWNER/$REPO/rulesets" --data @body.json
