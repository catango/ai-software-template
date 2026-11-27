#!/usr/bin/env bash
set -euo pipefail
"$GH" api "repos/$OWNER/$REPO/pulls/$N/merge" -X PUT
