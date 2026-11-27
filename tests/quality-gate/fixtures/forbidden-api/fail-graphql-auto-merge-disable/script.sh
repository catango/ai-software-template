#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query='mutation { disablePullRequestAutoMerge(input: {}) { clientMutationId } }'
