#!/usr/bin/env bash
set -euo pipefail
gh api -X PUT "orgs/$ORG/actions/secrets/$NAME"
