#!/usr/bin/env bash
set -euo pipefail
gh api graphql -f query='mutation { enablePullRequestAutoMerge(input: {}) { clientMutationId } }'
