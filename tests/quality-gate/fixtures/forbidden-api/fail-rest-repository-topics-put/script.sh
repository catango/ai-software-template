#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "repos/$OWNER/$REPO/topics" -f names[]=x
