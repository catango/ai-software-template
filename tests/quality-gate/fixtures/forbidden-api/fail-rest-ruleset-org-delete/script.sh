#!/usr/bin/env bash
set -euo pipefail
gh api -X DELETE "orgs/$ORG/rulesets/$ID"
